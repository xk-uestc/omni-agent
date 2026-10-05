"""Live homepage protocol acceptance; development fixtures, not official scores."""
import json
import uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
session='home-context-'+uuid.uuid4().hex
records=[]
def post(path,payload):
    payload={'session_id':session,**payload}
    with urlopen(Request('http://127.0.0.1:8030'+path,
        data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json'}),timeout=300) as response:
        body=json.load(response)
    records.append({'endpoint':path,'status':body['status'],'effective_question':body['effective_question'],
                    'context_resolution':body.get('context_resolution'),'result':body.get('result')})
    print(json.dumps({k:v for k,v in records[-1].items() if k!='result'},ensure_ascii=False),flush=True)
    return body
def query(question,reset=False):
    return post('/api/v1/omni/query',{'question':question,'reset_context':reset})
def clarify(body,value,time=None):
    return post('/api/v1/omni/clarify',{'original_question':body['effective_question'],
        'clarification_code':body['result']['clarification_code'],'selected_value':value,'selected_time':time})

passed=False
try:
    body=query('销售额趋势')
    assert body['status']=='clarification'
    body=clarify(body,'year','2025')
    assert body['status']=='clarification' and '2025年' in body['effective_question']
    body=clarify(body,'monthly_trend')
    assert body['status']=='ok' and '2025年' in body['effective_question']
    body=query('2025年华东销售额',True)
    assert body['status']=='ok'
    body=query('那华南呢')
    assert body['status']=='ok' and '2025' in body['effective_question'] and '华南' in body['effective_question']
    assert body['result']['plan']['metric_column']=='sales_amount'
    passed=True
finally:
    (ROOT/'runtime/home-context-http-20261004.json').write_text(json.dumps(
        {'not_official_benchmark':True,'passed':passed,'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
