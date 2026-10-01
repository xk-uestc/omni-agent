"""Opt-in localhost HTTP acceptance; synthetic results, never credentials."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]


def check_stream(base):
    started = time.monotonic()
    payload = {'question': '2025年华东地区销售额', 'use_context': False}
    events, current, done = [], None, None
    with requests.post(base+'/api/v1/agent/query/stream', json=payload, stream=True,
                       timeout=(10, 330), allow_redirects=False) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            text = line.decode('utf-8')
            if text.startswith('event: '):
                current = text[7:]
            elif text.startswith('data: '):
                value = json.loads(text[6:])
                events.append({'event': current, 'data': value})
                if current == 'done':
                    done = value
    structured = (done or {}).get('structured', {})
    checks = {'done_received': done is not None, 'no_error': not any(e['event']=='error' for e in events),
              'real_sql_plan': structured.get('plan', {}).get('planner_source')=='model_validated',
              'correct_value': structured.get('rows')==[{'销售额': 29584}],
              'actual_progress': any(e['event']=='trace' for e in events)}
    return {'id': 'http_stream_sql', 'checks': checks, 'pass': all(checks.values()),
            'events': events, 'duration_seconds': round(time.monotonic()-started, 3)}


def check_document(base):
    started = time.monotonic()
    response = requests.post(base+'/api/v1/omni/query',
        json={'question': '标准硬件产品保修期有多久？'}, timeout=(10, 330), allow_redirects=False)
    response.raise_for_status()
    data = response.json()
    result = data.get('result', {})
    checks = {'route': data.get('route')=='document', 'real_router': data.get('planner_source')=='model_validated',
              'status': result.get('status')=='ok', 'real_generation': result.get('answer_mode')=='model_grounded',
              'expected_fact': '12个月' in result.get('answer', ''), 'sources': bool(result.get('citations'))}
    return {'id': 'http_document_generation', 'checks': checks, 'pass': all(checks.values()),
            'result': data, 'duration_seconds': round(time.monotonic()-started, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8030')
    parser.add_argument('--output', type=Path, default=ROOT/'docs/HTTP_MODEL_ACCEPTANCE_REPORT.json')
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.username or parsed.password:
        parser.error('Only a localhost HTTP service is allowed')
    base = args.base_url.rstrip('/')
    health_response = requests.get(base+'/health', timeout=10, allow_redirects=False)
    health_response.raise_for_status()
    health = health_response.json()
    if health.get('generation', {}).get('model') != 'gpt-6-luna':
        parser.error('Start the program with the allowed gpt-6-luna configuration first')
    pages = {}
    for path in ('/', '/knowledge.html', '/capabilities.html', '/api/v1/specification'):
        response = requests.get(base+path, timeout=10, allow_redirects=False)
        pages[path] = {'status': response.status_code, 'bytes': len(response.content)}
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda fn: fn(base), (check_stream, check_document)))
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'actual_http_sse_and_model_generation_synthetic_development_not_accuracy_benchmark',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'health': health, 'static_pages': pages,
        'cases': records, 'passed': sum(row['pass'] for row in records), 'total': len(records),
        'ok': all(row['pass'] for row in records) and all(row['status']==200 for row in pages.values())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'passed': report['passed'], 'total': report['total'],
                      'cases': [{'id': row['id'], 'checks': row['checks']} for row in records]}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
