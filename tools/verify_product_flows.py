"""Replay real Omni HTTP/SSE flows. Model-dependent cases require --with-model.

Prepare and ingest the documented local samples first. This script never mocks
an answer, seeds data, updates Gold, or relaxes the server's verification.
"""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    'overview': ('帮我分析这个文件。', 'local-warranty', False),
    'table': ('高级熟练工的一般地区值是多少？', 'local-labor', False),
    'real-pdf': ('2024年国家统计局召开了多少场国民经济运行情况新闻发布会？', 'public-stats-disclosure-2024', True),
    'formula': ('根据文档《经营指标公式》的客单价公式，从数据库查询2025年华东销售额合计和订单数，再计算客单价。', None, True),
    'cross-source': ('先查询2025年销售额排名第一的地区，再检索该地区的销售经验', None, True),
    'cross-source-covered': ('先查询2025年华东、华南和华北销售额排名第一的地区，再检索该地区的销售经验', None, True),
    'insurance': ('根据教材公式和参数计算运输保险费。', None, True),
    'clarify': ('帮我看看利润', None, True),
}


def events(raw):
    for block in raw.replace('\r\n', '\n').split('\n\n'):
        name, data = 'message', []
        for line in block.splitlines():
            if line.startswith('event:'):
                name = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if data:
            yield name, json.loads('\n'.join(data))


def validate(case, response):
    result = response.get('result') or {}
    if case == 'clarify':
        return response.get('status') == 'clarification' and bool(result.get('clarification'))
    if response.get('status') != 'ok':
        return False
    if case == 'overview':
        return result.get('answer_mode') == 'document_structure_overview' and bool(result.get('citations'))
    if case == 'table':
        return bool(result.get('citations')) and (str(result.get('table_fact', {}).get('raw_value')) == '10.26' or any(str(value.get('value')) == '10.26' for value in result.get('results', {}).values()))
    if case == 'real-pdf':
        return bool(result.get('citations')) and bool(re.search(r'(?<!\d)10\s*场', result.get('answer', '')))
    completed = {item.get('tool') for item in result.get('trace', []) if item.get('status') == 'complete'}
    return {'sql', 'document_formula', 'calculate'}.issubset(completed) if case in {'formula', 'insurance'} else {'sql', 'search'}.issubset(completed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8030')
    parser.add_argument('--with-model', action='store_true', help='Allow cases that use the configured paid model; deployment budget still applies.')
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/product_acceptance' / time.strftime('%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    selected = args.case or [name for name, (_, _, paid) in CASES.items() if name not in {'cross-source-covered', 'insurance'} and (args.with_model or not paid)]
    if any(CASES[name][2] for name in selected) and not args.with_model:
        parser.error('Model-dependent cases require --with-model and a configured, authorized model budget.')
    args.output.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    summaries = []
    for name in selected:
        question, document_id, _ = CASES[name]
        payload = {'question': question, 'session_id': 'product-' + uuid.uuid4().hex,
                   'reset_context': True, 'document_id': document_id}
        request = urllib.request.Request(args.base_url.rstrip('/') + '/api/v1/omni/query/stream',
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
        started = time.perf_counter()
        response, raw, status, content_type = {}, '', None, None
        try:
            with opener.open(request, timeout=240) as stream:
                status, content_type = stream.status, stream.headers.get('Content-Type')
                raw = stream.read().decode('utf-8')
            observed = list(events(raw))
            response = next((data for event, data in reversed(observed) if event == 'done'), {})
            if not response:
                response = {'status': 'error', 'detail': next((data for event, data in reversed(observed) if event == 'error'), 'SSE ended without a final event')}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            response = {'status': 'transport_error', 'detail': str(exc)}
            observed = []
        record = {'case': name, 'request': payload, 'http_status': status, 'content_type': content_type,
                  'seconds': round(time.perf_counter() - started, 3), 'response': response}
        (args.output / (name + '.sse')).write_text(raw, encoding='utf-8')
        (args.output / (name + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        passed = status == 200 and 'text/event-stream' in (content_type or '') and validate(name, response)
        row = {'case': name, 'passed': passed, 'status': response.get('status'), 'route': response.get('route'),
               'planner_source': response.get('planner_source'), 'seconds': record['seconds'],
               'trace_events': sum(event == 'trace' for event, _ in observed)}
        summaries.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (args.output / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(row['passed'] for row in summaries) else 1


if __name__ == '__main__':
    raise SystemExit(main())
