"""Live explicit history-pair comparison, checked against independent SQLite."""
from pathlib import Path
from urllib.request import Request,urlopen
import json
import sqlite3
import uuid

ROOT=Path(__file__).resolve().parents[1]
session='selected-pair-'+uuid.uuid4().hex
report=ROOT/'runtime'/('selected-comparison-http-'+uuid.uuid4().hex+'.json')
cases=[]


def query(question,status,expected=None,code=None):
    with urlopen(Request('http://127.0.0.1:8030/api/v1/omni/query',
        data=json.dumps({'question':question,'session_id':session},ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'}),timeout=300) as response:data=json.load(response)
    cases.append({key:data.get(key) for key in ['question','effective_question','status','route','result','trace']})
    assert data['status']==status
    if expected:
        row=data['result']['rows'][0]
        assert [row['基准值'],row['比较值'],row['差值']]==[expected[0],expected[1],expected[1]-expected[0]]
        assert data['route']=='comparison'
    if code:
        assert data['result']['clarification_code']==code
        assert not data['result']['rows']
    print(json.dumps({'question':question,'status':data['status']},ensure_ascii=False),flush=True)


passed=False
try:
    with urlopen('http://127.0.0.1:8030/health',timeout=10) as response:health=json.load(response)
    assert health['database']=='demo_sales.sqlite'
    with sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        expected=[connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE substr(order_date,1,4)=? AND region=?',args).fetchone()[0]
                  for args in [('2024','华东'),('2025','华南')]]
    for question in ['2024年华东销售额','2025年华北销售额','2025年华南销售额']:query(question,'ok')
    query('比较倒数第三次和倒数第一次SQL查询','ok',expected)
    query('把基准换成较晚结果','ok',expected[::-1])
    query('比较SQL查询“2024年华东销售额”和“2025年华南销售额”','ok',expected)
    query('比较倒数第九次和倒数第一次SQL查询','clarification',code='comparison_selected_reference_unavailable')
    passed=True
finally:
    report.write_text(json.dumps({'not_official_benchmark':True,'passed':passed,'session_id':session,
        'independent_expected_values':locals().get('expected'),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
