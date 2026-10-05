"""Live clarification predicate edits, independently verified with SQLite."""
import hashlib
import json
from pathlib import Path
import sys
import uuid
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from verify_pending_task_resume_http import reference


def main():
    session = 'pending-filter-' + uuid.uuid4().hex
    names = ['pending_filter_edit.py', 'pending_scope_edit.py', 'omni_agent.py',
             'pending_source_validation.py', 'pending_task_resume.py', 'session.py',
             'pending_task_store.py', 'app.py']
    paths = [ROOT / 'ict-track8/backend' / name for name in names]
    paths += [ROOT / 'ict-track8/frontend' / name for name in
              ['app.js', 'conversation-context.js', 'index.html']]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    records = []
    report = ROOT / 'runtime' / f'pending-filter-http-{uuid.uuid4().hex}.json'

    def send(question, *, code=None, expected=None, scope=None):
        request = Request('http://127.0.0.1:8030/api/v1/omni/query',
                          data=json.dumps({'question': question, 'session_id': session}, ensure_ascii=False).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=300) as response:
            body = json.load(response)
        result = body.get('result', {})
        passed = (body.get('status') == 'clarification' and result.get('clarification_code') == code
                  and result.get('sql') is None) if code else (
                      body.get('status') == 'ok' and result.get('rows') == expected)
        if scope is not None:
            passed = passed and body.get('effective_question') == scope
        records.append({'question': question, 'passed': passed, 'response': body, 'expected_rows': expected})
        print(json.dumps({'question': question, 'passed': passed, 'status': body.get('status')}, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError('Actual response did not match independent expectation')
        return body

    try:
        first = send('2025年华东销售额趋势', code='missing_time_grain')
        added = send('增加渠道筛选为线上', code='missing_time_grain')
        send('选项有什么区别', code='missing_time_grain', scope=added['effective_question'])
        gold = reference("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 "
                         'FROM sales_orders WHERE order_date>=? AND order_date<? AND channel=? GROUP BY 1 ORDER BY 1',
                         ('2024-01-01', '2025-01-01', '线上'))
        send('删除地区筛选，时间改成2024年，按月统计', expected=gold)
        send(f"继续待补查询编号{first['query_reference_id']}", code='pending_reference_completed')
        send(f"继续待补查询编号{added['query_reference_id']}", code='pending_reference_completed')
        pending = send('2025年华东销售额趋势', code='missing_time_grain')
        send('删除地区筛选，增加渠道筛选为火星，按月统计', code='missing_time_grain',
             scope=pending['effective_question'])
        gold = reference("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 "
                         'FROM sales_orders WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',
                         ('2025-01-01', '2026-01-01', '华东'))
        send('按月统计', expected=gold)
        send('2025年排除华东销售额趋势', code='missing_time_grain')
        gold = reference("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 "
                         'FROM sales_orders WHERE order_date>=? AND order_date<? GROUP BY 1 ORDER BY 1',
                         ('2025-01-01', '2026-01-01'))
        send('删除地区筛选，按月统计', expected=gold)
    finally:
        stable = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark': True, 'source_hashes': hashes,
                                     'source_stable': stable, 'turns': records}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(str(report), flush=True)
    return 0 if stable and all(record['passed'] for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
