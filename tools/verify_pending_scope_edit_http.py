"""Real API dialogue edits, checked against independent SQLite references."""
import argparse,hashlib,json,sqlite3,sys,uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.seed import initialize_database


def reference(sql,parameters):
    connection=sqlite3.connect(initialize_database(ROOT/'runtime/pending-scope-edit-reference.sqlite'))
    connection.row_factory=sqlite3.Row
    try:return [dict(row) for row in connection.execute(sql,parameters)]
    finally:connection.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--atomic',action='store_true');args=parser.parse_args()
    session='pending-scope-edit-'+uuid.uuid4().hex
    source_files=['ict-track8/backend/pending_scope_edit.py','ict-track8/backend/omni_agent.py',
                  'ict-track8/backend/sql_history_scope.py']
    hashes={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in source_files}
    month_reference=reference('SELECT strftime(\'%Y-%m\',order_date) AS 月份,COUNT(order_id) AS 订单数 '
        'FROM sales_orders WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',
        ('2024-01-01','2025-01-01','华南'))
    sales_reference=reference('SELECT SUM(sales_amount) AS 销售额 FROM sales_orders '
        'WHERE order_date>=? AND order_date<? AND region=?',('2025-01-01','2026-01-01','华北'))
    turns=[
        ('2025年华东销售额趋势',False,'2025年华东销售额趋势',None,'missing_time_grain'),
        ('时间改成2024年，地区改成火星',False,'2025年华东销售额趋势',None,'missing_time_grain'),
        ('那把时间改成2024年吧',False,'2024年华东销售额趋势',None,'missing_time_grain'),
        ('地区改成华南，指标改成订单数',False,'2024年华南订单数趋势',None,'missing_time_grain'),
        ('选项有什么区别',False,'2024年华南订单数趋势',None,'missing_time_grain'),
        ('按月统计',False,None,month_reference,None),
        ('2025年华北销售额',True,'2025年华北销售额',sales_reference,None),
    ]
    if args.atomic:
        turns.extend([
            ('2025年华东销售额趋势',True,'2025年华东销售额趋势',None,'missing_time_grain'),
            ('时间改成2024年，地区改成华南，指标改成订单数，按月统计',False,None,month_reference,None),
        ])
    records=[]
    for question,reset,scope,expected,code in turns:
        request=Request('http://127.0.0.1:8030/api/v1/omni/query',
            data=json.dumps({'question':question,'session_id':session,'reset_context':reset},ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:data=json.load(response)
        result=data.get('result',{})
        if code:
            passed=data.get('status')=='clarification' and result.get('clarification_code')==code and result.get('sql') is None
        else:passed=data.get('status')=='ok' and result.get('rows')==expected
        if scope is not None:passed=passed and data.get('effective_question')==scope
        if question=='时间改成2024年，地区改成火星':
            passed=passed and data.get('context_resolution',{}).get('mode')=='pending_sql_clarification_retained'
        elif question in {'那把时间改成2024年吧','地区改成华南，指标改成订单数'}:
            passed=passed and data.get('planner_source')=='PendingScopeEditAgent'
        record={'question':question,'passed':passed,'status':data.get('status'),
            'effective_question':data.get('effective_question'),'planner_source':data.get('planner_source'),
            'context_resolution':data.get('context_resolution'),'result':result,'expected_rows':expected}
        records.append(record)
        print(json.dumps({key:value for key,value in record.items() if key not in {'result','expected_rows'}},ensure_ascii=False),flush=True)
    stable=all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==value for path,value in hashes.items())
    report=ROOT/'runtime'/f'pending-scope-edit-http-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,'source_code_sha256':hashes,
        'source_stable':stable,'session_id':session,'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
    return 0 if stable and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
