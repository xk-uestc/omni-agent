"""Runtime comparison clarification evidence, not an official benchmark."""
import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    session = 'comparison-help-' + uuid.uuid4().hex
    report = ROOT / f'runtime/comparison-help-http-{uuid.uuid4().hex}.json'
    records = []

    def query(question, check):
        request = Request('http://127.0.0.1:8030/api/v1/omni/query',
            data=json.dumps({'question': question, 'session_id': session}, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=300) as response:
            body = json.load(response)
        record = {key: body.get(key) for key in ('question', 'effective_question', 'route', 'status', 'trace')}
        record.update(clarification_code=body.get('result', {}).get('clarification_code'),
                      rows=body.get('result', {}).get('rows'), passed=bool(check(body)))
        records.append(record)
        report.write_text(json.dumps({'not_official_benchmark': True, 'session_id': session,
            'turns': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False), flush=True)
        return body

    left = query('2025年华东销售额', lambda b: b['status']=='ok')['result']['rows'][0]['销售额']
    right = query('2025年华南销售额', lambda b: b['status']=='ok')['result']['rows'][0]['销售额']
    def original(b):
        rows = b.get('result', {}).get('rows', [])
        return b['route']=='comparison' and b['status']=='ok' and len(rows)==1 and (
            rows[0]['基准值'], rows[0]['比较值'], rows[0]['差值'])==(left, right, right-left)
    query('比较刚才两次查询', original)
    def pending_role(b):
        return b['route']=='comparison' and b['status']=='clarification' and b['result']['clarification_code']=='comparison_edit_role_required'
    query('换成2024年', pending_role)
    for text in ['好的', '这些选项是什么意思', '继续吧']:
        query(text, lambda b: pending_role(b) and b['trace'][0].get('executed') is False
              and b['effective_question']=='换成2024年')
    query('取消修改', original)
    def pending_grain(b):
        return b['route']=='comparison' and b['status']=='clarification' and b['result']['clarification_code']=='missing_time_grain' and b['effective_question']=='2025年华东销售额趋势'
    query('把基准查询改成完整问题：2025年华东销售额趋势', pending_grain)
    for text in ['确认', '怎么选', '按季度']:
        query(text, lambda b: pending_grain(b) and b['trace'][0].get('executed') is False)
    query('取消修改', original)
    print(str(report), flush=True)
    return 0 if all(row['passed'] for row in records) else 1


if __name__=='__main__':
    raise SystemExit(main())
