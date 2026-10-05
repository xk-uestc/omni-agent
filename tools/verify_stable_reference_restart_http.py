"""Use an earlier actual HTTP query ID after the service process restarts."""
from pathlib import Path
from urllib.request import Request,urlopen
import json,sqlite3,uuid
ROOT=Path(__file__).resolve().parents[1]
prior=json.loads((ROOT/'runtime/stable-reference-http-f44d63164925439dbe4c334852c264b8.json').read_text(encoding='utf-8'))
assert prior['passed']
identifier=prior['cases'][0]['query_reference_id']
question=f'回到SQL查询编号{identifier}，那华北呢'
report=ROOT/'runtime'/('stable-reference-restart-http-'+uuid.uuid4().hex+'.json')
with urlopen(Request('http://127.0.0.1:8030/api/v1/omni/query',
    data=json.dumps({'question':question,'session_id':prior['session_id']},ensure_ascii=False).encode(),
    headers={'Content-Type':'application/json'}),timeout=300) as response:data=json.load(response)
with sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
    expected=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE substr(order_date,1,4)='2025' AND region='华北'").fetchone()[0]
observed=list(data['result']['rows'][0].values())[0] if data['status']=='ok' else None
passed=data['status']=='ok' and data['context_resolution']['context_reference']['turn_id']==identifier and observed==expected
report.write_text(json.dumps({'not_official_benchmark':True,'prior_session':prior['session_id'],'prior_query_id':identifier,
    'passed':passed,'expected':expected,'observed':observed,'response':data},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':passed,'expected':expected,'observed':observed,'report':str(report)},ensure_ascii=False))
if not passed:raise SystemExit(1)
