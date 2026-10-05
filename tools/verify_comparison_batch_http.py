"""Live dual-operand edits; values checked independently with SQLite."""
from pathlib import Path
from urllib.request import Request,urlopen
import sqlite3
import json
import uuid

ROOT=Path(__file__).resolve().parents[1]
session='batch-edit-'+uuid.uuid4().hex
report=ROOT/'runtime'/('comparison-batch-http-'+uuid.uuid4().hex+'.json')
cases=[]

def query(question):
    with urlopen(Request('http://127.0.0.1:8030/api/v1/omni/query',
        data=json.dumps({'question':question,'session_id':session},ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'}),timeout=300) as response:body=json.load(response)
    cases.append({key:body.get(key) for key in ['question','status','route','effective_question','result','trace']})
    print(json.dumps({'question':question,'status':body['status']},ensure_ascii=False),flush=True)
    assert body['status']=='ok'
    return body

passed=False
try:
    with sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        expected=[connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE substr(order_date,1,4)=? AND region=?',
            ('2024',region)).fetchone()[0] for region in ['华东','华南']]
    query('2025年华东销售额')
    query('2025年华南销售额')
    query('比较刚才两次查询')
    body=query('把基准查询改成2024年；把比较查询改成2024年')
    row=body['result']['rows'][0]
    assert [row['基准值'],row['比较值'],row['差值']]==[expected[0],expected[1],expected[1]-expected[0]]
    assert len(body['result']['batch_edit_evidence'])==2
    assert all(change['query_status']=='ok' for change in body['result']['batch_edit_evidence'])
    recovered=query('两者差多少')
    assert recovered['result']['rows']==body['result']['rows']
    passed=True
finally:
    report.write_text(json.dumps({'not_official_benchmark':True,'passed':passed,'session_id':session,
        'independent_sqlite_reference':locals().get('expected'),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
