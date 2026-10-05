"""Live history-reference acceptance; demo data, never an official score."""
import json
import argparse
import sqlite3
import uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--session',help='Verify final routing in a previously checked session, without repeating initial SQL queries')
args=parser.parse_args()
report=ROOT/'runtime'/('history-reference-http-'+uuid.uuid4().hex+'.json')
session=args.session or 'history-ref-'+uuid.uuid4().hex
cases=[]


def query(question,expected_status,expected_value=None,expected_reason=None):
    with urlopen(Request('http://127.0.0.1:8030/api/v1/omni/query',
        data=json.dumps({'session_id':session,'question':question},ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'}),timeout=300) as response:
        data=json.load(response)
    case={key:data.get(key) for key in ['question','status','route','effective_question','context_resolution','result','trace']}
    cases.append(case)
    assert data['status']==expected_status,data['status']
    if expected_value is not None:
        assert data['result']['rows']==[{'销售额':expected_value}],data['result']['rows']
    if expected_reason:
        assert data['context_resolution']['reason']==expected_reason
        assert not data['result'].get('rows')
        assert data['trace'][0]['source']=='rules_basic'
    print(json.dumps({'question':question,'status':data['status'],'effective_question':data['effective_question']},ensure_ascii=False),flush=True)


passed=False
try:
    with sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        expected=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>='2025-01-01' AND order_date<'2026-01-01' AND region='华南'").fetchone()[0]
    if not args.session:
        query('2025年华东销售额','ok')
        query('2024年华北订单数','ok')
        query('回到倒数第二次SQL查询，那华南呢','ok',expected)
    query('回到SQL查询“2025年华东销售额”，那华南呢','ok',expected)
    query('回到第一次SQL查询，那华南呢','clarification',expected_reason='requested_sql_reference_expression_unsupported')
    query('回到倒数第九次SQL查询，那华南呢','clarification',expected_reason='requested_sql_reference_not_available')
    passed=True
finally:
    report.write_text(json.dumps({'not_official_benchmark':True,'passed':passed,
        'session_id':session,'independent_sqlite_expected_value':locals().get('expected'),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
