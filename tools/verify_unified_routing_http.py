"""Live unified SQL/RAG acceptance; isolated sessions and read-only references."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import re
import time
import uuid

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8031')
    parser.add_argument('--only-fusion', action='store_true')
    args = parser.parse_args()
    base = args.base.rstrip('/')
    output = ROOT / 'runtime' / ('unified-http-' + uuid.uuid4().hex)
    output.mkdir()
    health = requests.get(base + '/health', timeout=10).json()
    records, checks = [], []

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    def query(question, session):
        started = time.perf_counter()
        events, result = [], None
        with requests.post(base + '/api/v1/omni/query/stream', json={
                'question': question, 'session_id': session}, stream=True, timeout=(10, 310)) as response:
            response.raise_for_status()
            response.encoding = 'utf-8'
            event = None
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:'):
                    payload = json.loads(line[5:])
                    events.append({'event': event, 'data': payload})
                    if event == 'done':
                        result = payload
        elapsed = round(time.perf_counter() - started, 3)
        records.append({'question': question, 'elapsed_seconds': elapsed, 'response': result, 'events': events})
        (output / (hashlib.sha256((session + question).encode()).hexdigest()[:16] + '.json')).write_text(
            json.dumps(records[-1], ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'question': question, 'seconds': elapsed,
                          'route': (result or {}).get('route'), 'status': (result or {}).get('status')},
                         ensure_ascii=False), flush=True)
        if result is None:
            raise ValueError('SSE did not return a final response')
        return result

    def citations_verified(citations):
        if not citations:
            return False
        for item in citations:
            metadata = item.get('metadata', {})
            identifier = metadata.get('document_id')
            document = requests.get(base + '/api/v1/knowledge/documents/' + identifier, timeout=10).json()
            if document['sha256'] != metadata.get('source_sha256'):
                return False
            texts = [chunk['text'] for chunk in document['chunks']]
            texts += [chunk.get('metadata', {}).get('raw_page_text', '') for chunk in document['chunks']]
            quote = re.sub(r'\s+', '', item.get('snippet', ''))
            if quote and not any(quote in re.sub(r'\s+', '', text) for text in texts):
                return False
        return True

    def sql_flow():
        session = 'unified-sql-' + uuid.uuid4().hex
        first = query('2025年各地区销售额排名', session)
        with sqlite3.connect('file:' + str(ROOT / 'ict-track8/data/demo_sales.sqlite') + '?mode=ro', uri=True) as db:
            reference = db.execute("SELECT region, ROUND(SUM(sales_amount),2), DENSE_RANK() OVER (ORDER BY SUM(sales_amount) DESC) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region ORDER BY SUM(sales_amount) DESC").fetchall()
        check('SQL ranking matches independent database aggregation',
              [(r['region'], r['销售额'], r['排名']) for r in first['result'].get('rows', [])] == reference)
        row = next(r for r in first['result']['rows'] if r['region'] == '西南')
        for question in ['西南排名多少', '西南销售额多少']:
            reply = query(question, session)
            check(question, reply['status'] == 'ok' and reply['result']['rows'] == [row]
                  and reply['result']['sql'] is None and reply['routing']['strategy'] == 'verified_result_lookup')
        changed = query('2024年西南销售额', session)
        check('new year is freshly queried', changed['status'] == 'ok' and changed['result'].get('sql')
              and changed['result']['rows'][0]['销售额'] != row['销售额'])
        definition = query('销售额是什么意思', session)
        check('metric definition switches to documents with real citations',
              definition['route'] == 'document' and definition['status'] == 'ok'
              and citations_verified(definition['result'].get('citations', [])))

    def document_flow():
        session = 'unified-doc-' + uuid.uuid4().hex
        first = query('查询文档“销售冠军经验分享”：华东团队采用什么销售方法', session)
        check('named source answer verified', first['status'] == 'ok'
              and citations_verified(first['result'].get('citations', [])))
        followup = query('那华南的销售方法呢', session)
        check('document followup remains source bound', followup['status'] == 'ok'
              and {c['metadata']['document_id'] for c in followup['result'].get('citations', [])} == {'champion-method'}
              and '渠道伙伴培训' in followup['result'].get('answer', ''))
        query('2025年各地区销售额排名', session)
        query('华北排名多少', session)
        bridge = query('它的销售方法是什么', session)
        check('SQL entity becomes RAG scope', bridge['status'] == 'ok' and bridge['route'] == 'document'
              and bridge['result'].get('sql_entity_evidence', {}).get('entity') == '华北'
              and citations_verified(bridge['result'].get('citations', [])))

    def fusion_flow():
        result = query('先从数据库查询2025年销售额排名第一的地区，再检索该地区销售冠军经验',
                       'unified-fusion-' + uuid.uuid4().hex)
        steps = result['result'].get('results', {})
        sqls = [step for step in steps.values() if step.get('sql')]
        searches = [step for step in steps.values() if 'hits' in step]
        check('live model builds and executes dependent SQL to RAG plan', result['route'] == 'fusion'
              and result['status'] == 'ok' and len(sqls) == 1 and bool(searches)
              and sqls[0]['rows'][0]['region'] == '东南'
              and result['result'].get('user_constraint_validation', {}).get('status') == 'verified')
        check('fusion retrieves source verified original excerpts',
              bool(searches) and citations_verified(searches[0].get('hits', [])))
        # Retrieval success is not evidence that methods for an undocumented
        # winner exist. Keep full excerpts in the report for human assessment.

    check('6-luna enabled', health['generation']['model'] == 'gpt-6-luna')
    with ThreadPoolExecutor(max_workers=3) as pool:
        flows = (fusion_flow,) if args.only_fusion else (sql_flow, document_flow, fusion_flow)
        futures = [pool.submit(fn) for fn in flows]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                check('live flow completed (' + type(exc).__name__ + ')', False)
    report = {'base': base, 'health': health, 'checks': checks, 'records': records,
              'all_passed': all(c['passed'] for c in checks)}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(output / 'report.json'), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
