"""Live stable query IDs, repeated questions and independent SQLite values."""
from pathlib import Path
from urllib.request import Request,urlopen
import json,sqlite3,uuid,re
ROOT=Path(__file__).resolve().parents[1]
session='stable-id-'+uuid.uuid4().hex
report=ROOT/'runtime'/('stable-reference-http-'+uuid.uuid4().hex+'.json')
cases=[]
def query(question,status='ok',sid=None):
    with urlopen(Request('http://127.0.0.1:8030/api/v1/omni/query',
        data=json.dumps({'question':question,'session_id':sid or session},ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'}),timeout=300) as response:data=json.load(response)
    cases.append({key:data.get(key) for key in ['question','effective_question','status','route','query_reference_id','context_resolution','result']})
    assert data['status']==status
    if status=='ok' and data['route']=='sql':assert re.fullmatch(r'q_[a-f0-9]{32}',data['query_reference_id'])
    print(json.dumps({'question':question,'status':data['status'],'reference_id':data.get('query_reference_id')},ensure_ascii=False),flush=True)
    return data
passed=False
try:
    with urlopen('http://127.0.0.1:8030/health',timeout=10) as response:health=json.load(response)
    assert health['database']=='demo_sales.sqlite'
    with sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        expected=[connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE substr(order_date,1,4)=? AND region=?',
            ('2025',region)).fetchone()[0] for region in ['华东','华南']]
    first=query('2025年华东销售额');second=query('2025年华东销售额')
    assert first['query_reference_id']!=second['query_reference_id']
    third=query(f"回到SQL查询编号{first['query_reference_id']}，那华南呢")
    assert third['context_resolution']['context_reference']['turn_id']==first['query_reference_id']
    comparison=query(f"比较SQL查询编号{first['query_reference_id']}和{third['query_reference_id']}")
    row=comparison['result']['rows'][0]
    assert [row['基准值'],row['比较值'],row['差值']]==[expected[0],expected[1],expected[1]-expected[0]]
    rejected=query(f"回到SQL查询编号{first['query_reference_id']}，那华南呢",'clarification',session+'-other')
    assert rejected['context_resolution']['reason']=='requested_sql_reference_not_available'
    assert not rejected['result'].get('sql')
    passed=True
finally:
    report.write_text(json.dumps({'not_official_benchmark':True,'passed':passed,'session_id':session,
        'independent_expected_values':locals().get('expected'),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report),flush=True)
