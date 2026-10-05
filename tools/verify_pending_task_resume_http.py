"""Interrupted SQL tasks through API, with independent SQLite result checks."""
import hashlib,json,sqlite3,sys,uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.seed import initialize_database


def reference(sql,parameters):
    connection=sqlite3.connect(initialize_database(ROOT/'runtime/pending-resume-reference.sqlite'))
    connection.row_factory=sqlite3.Row
    try:return [dict(row) for row in connection.execute(sql,parameters)]
    finally:connection.close()


def main():
    session='pending-resume-'+uuid.uuid4().hex;other='pending-resume-other-'+uuid.uuid4().hex
    files=['ict-track8/backend/pending_task_resume.py','ict-track8/backend/omni_agent.py',
           'ict-track8/backend/pending_scope_edit.py','ict-track8/backend/sql_history_scope.py',
           'ict-track8/backend/session.py','ict-track8/backend/pending_task_store.py']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    records=[]
    def ask(question,*,expected=None,code=None,scope=None,identifier=None,mode=None,sid=session):
        request=Request('http://127.0.0.1:8030/api/v1/omni/query',
            data=json.dumps({'question':question,'session_id':sid},ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:data=json.load(response)
        result=data.get('result',{});audit=data.get('context_resolution',{})
        passed=(data.get('status')=='clarification' and result.get('clarification_code')==code and result.get('sql') is None
                if code else data.get('status')=='ok' and result.get('rows')==expected)
        if scope is not None:passed=passed and data.get('effective_question')==scope
        if identifier is not None:passed=passed and audit.get('pending_task_reference',{}).get('turn_id')==identifier
        if mode:passed=passed and audit.get('mode')==mode
        record={'question':question,'passed':passed,'expected_rows':expected,'response':data}
        records.append(record)
        print(json.dumps({'question':question,'passed':passed,'status':data.get('status'),
            'effective_question':data.get('effective_question'),'reference':audit.get('pending_task_reference')},ensure_ascii=False),flush=True)
        return data
    a=ask('2025年华东销售额趋势',code='missing_time_grain')['query_reference_id']
    b=ask('2024年华南订单数趋势',code='missing_time_grain')['query_reference_id']
    if '--long' in sys.argv:
        for _ in range(12):
            ask('继续待补查询编号q_'+uuid.uuid4().hex,code='pending_reference_unavailable')
    gold=reference('SELECT SUM(sales_amount) AS 销售额 FROM sales_orders '
        'WHERE order_date>=? AND order_date<? AND region=?',('2025-01-01','2026-01-01','华北'))
    completed=ask('2025年华北销售额',expected=gold)['query_reference_id']
    ask(f'继续待补查询编号{a}',code='missing_time_grain',scope='2025年华东销售额趋势',identifier=a,
        mode='server_verified_pending_task_resume')
    monthly=reference('SELECT strftime(\'%Y-%m\',order_date) AS 月份,SUM(sales_amount) AS 销售额 FROM sales_orders '
        'WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',('2025-01-01','2026-01-01','华东'))
    ask('按月统计',expected=monthly)
    if '--long' in sys.argv:
        ask(f'继续待补查询编号{a}',code='pending_reference_completed')
    annual=reference('SELECT strftime(\'%Y\',order_date) AS 年份,COUNT(order_id) AS 订单数 FROM sales_orders '
        'WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',('2025-01-01','2026-01-01','华南'))
    ask(f'继续待补查询编号{b}，时间改成2025年，按年统计',expected=annual,identifier=b)
    if '--long' in sys.argv:
        ask(f'继续待补查询编号{b}',code='pending_reference_completed')
    ask(f'继续待补查询编号{completed}',code='pending_reference_not_pending')
    ask(f'继续待补查询编号{a}，按月统计',code='pending_reference_unavailable',sid=other)
    stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
    prefix='long-pending-task-http' if '--long' in sys.argv else 'pending-task-resume-http'
    report=ROOT/'runtime'/f'{prefix}-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,'source_code_sha256':hashes,
        'source_stable':stable,'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
    return 0 if stable and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
