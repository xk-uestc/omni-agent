"""Actual UI selection endpoint, independently checked against SQLite."""
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import json
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_pending_task_resume_http import reference


def main():
    session='pending-ui-'+uuid.uuid4().hex
    records=[]
    files=['ict-track8/backend/app.py','ict-track8/backend/omni_agent.py',
           'ict-track8/backend/pending_source_validation.py','ict-track8/backend/pending_task_resume.py']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    def send(path,payload,*,code=None,expected=None):
        request=Request('http://127.0.0.1:8030'+path,data=json.dumps(payload).encode(),
                        headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:body=json.load(response)
        result=body.get('result',{})
        passed=(body.get('status')=='clarification' and result.get('clarification_code')==code
            and result.get('sql') is None) if code else body.get('status')=='ok' and result.get('rows')==expected
        records.append({'path':path,'request':payload,'passed':passed,'response':body,'expected_rows':expected})
        print(json.dumps({'path':path,'passed':passed,'status':body.get('status'),'code':result.get('clarification_code')}),flush=True)
        if not passed:raise ValueError('Verification failed; preserve actual response in report')
        return body
    report=ROOT/'runtime'/f'pending-ui-http-{uuid.uuid4().hex}.json'
    try:
        body=send('/api/v1/omni/query',{'session_id':session,'question':'销售额趋势'},code='missing_time_range')
        identifiers=[body['query_reference_id']]
        body=send('/api/v1/omni/query',{'session_id':session,'question':'解释一下'},code='missing_time_range')
        identifiers.append(body['query_reference_id'])
        body=send('/api/v1/omni/clarify',{'session_id':session,'original_question':body['effective_question'],
            'clarification_code':body['result']['clarification_code'],'selected_value':'year','selected_time':'2025'},
            code='missing_time_grain')
        identifiers.append(body['query_reference_id'])
        gold=reference("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 FROM sales_orders "
            'WHERE order_date>=? AND order_date<? GROUP BY 1 ORDER BY 1',('2025-01-01','2026-01-01'))
        body=send('/api/v1/omni/clarify',{'session_id':session,'original_question':body['effective_question'],
            'clarification_code':body['result']['clarification_code'],'selected_value':'monthly_trend'},expected=gold)
        for identifier in identifiers:
            send('/api/v1/omni/query',{'session_id':session,'question':f'继续待补查询编号{identifier}'},code='pending_reference_completed')
        send('/api/v1/omni/query',{'session_id':'foreign-'+session,'question':f'继续待补查询编号{identifiers[0]}'},
            code='pending_reference_unavailable')
    finally:
        stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,
            'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
