"""Exercise one five-turn dialogue against the running local model server."""
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def main():
    engine = Nl2SqlEngine(initialize_database(ROOT / 'runtime/context-polish-reference.sqlite'))
    session = 'context-polish-' + uuid.uuid4().hex
    records = []
    turns = [('2025年华东销售额', '2025年华东销售额'),
             ('再查华南', '2025年华南销售额'),
             ('同样2024年', '2024年华南销售额'),
             ('换成订单数', '2024年华南订单数'),
             ('按渠道分组', '2024年华南订单数 按渠道分组')]
    for question, expected_question in turns:
        payload = json.dumps({'question': question, 'session_id': session}, ensure_ascii=False).encode()
        request = Request('http://127.0.0.1:8030/api/v1/omni/query', data=payload,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=300) as response:
            result = json.load(response)
        expected = engine.answer(expected_question).to_dict()
        rows = result.get('result', {}).get('rows')
        record = {'question': question, 'effective_question': result.get('effective_question'),
                  'status': result.get('status'), 'context_resolution': result.get('context_resolution'),
                  'planning_source': result.get('planning_source'),
                  'model_calls': [attempt.get('api_audit', {})
                                  for trace in result.get('trace', [])
                                  for attempt in trace.get('attempts', [])],
                  'rows': rows, 'expected_rows': expected['rows'],
                  'passed': result.get('status') == 'ok' and rows == expected['rows']}
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    report = ROOT / 'runtime/context-polish-http-20261004.json'
    report.write_text(json.dumps({'not_official_benchmark': True, 'turns': records},
                                ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(item['passed'] for item in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
