"""Live interrupted comparison recovery, with independent SQLite references."""
import hashlib,json,sys,uuid
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_pending_task_resume_http import reference


def main():
    session='pending-comparison-'+uuid.uuid4().hex
    names=['omni_agent.py','pending_comparison_resume.py','pending_task_catalog.py','pending_task_store.py','conversation_comparison.py']
    hashes={name:hashlib.sha256((ROOT/'ict-track8/backend'/name).read_bytes()).hexdigest() for name in names}
    records=[]
    report=ROOT/'runtime'/f'pending-comparison-http-{uuid.uuid4().hex}.json'
    def ask(question,check,*,foreign=False):
        request=Request('http://127.0.0.1:8030/api/v1/omni/query',data=json.dumps({'question':question,
            'session_id':('foreign-' if foreign else '')+session},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:body=json.load(response)
        passed=bool(check(body));records.append({'question':question,'foreign':foreign,'passed':passed,'response':body})
        print(json.dumps({'question':question,'passed':passed,'route':body.get('route')},ensure_ascii=False),flush=True)
        if not passed:raise AssertionError('Actual response failed; report retained')
        return body
    sql='SELECT SUM(sales_amount) AS 销售额 FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?'
    def total(year,region):return reference(sql,(f'{year}-01-01',f'{year+1}-01-01',region))[0]['销售额']
    try:
        for region in ('华东','华南'):
            gold=[{'销售额':total(2025,region)}]
            ask(f'2025年{region}销售额',lambda b:b.get('status')=='ok' and b['result']['rows']==gold)
        ask('比较刚才两次查询',lambda b:b.get('status')=='ok' and b.get('route')=='comparison')
        pending=ask('换成2024年',lambda b:b['result'].get('clarification_code')=='comparison_edit_role_required')
        identifier=pending['query_reference_id']
        count=reference('SELECT COUNT(order_id) AS 订单数 FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?',('2025-01-01','2026-01-01','华北'))
        ask('2025年华北订单数',lambda b:b.get('status')=='ok' and b['result']['rows']==count)
        ask('查看待补问题',lambda b:b.get('route')=='tasks' and len(b.get('pending_tasks',[]))==1
            and b['pending_tasks'][0]['query_reference_id']==identifier and b['pending_tasks'][0]['available'])
        ask(f'继续待补查询编号{identifier}',lambda b:b['result'].get('clarification_code')=='pending_reference_unavailable',foreign=True)
        restored=ask(f'继续待补查询编号{identifier}',lambda b:b.get('route')=='comparison'
            and b['result'].get('clarification_code')=='comparison_edit_role_required'
            and b.get('context_resolution',{}).get('executed') is False)
        latest=restored['query_reference_id']
        left,right=total(2024,'华东'),total(2025,'华南')
        ask(f'继续待补查询编号{latest}，基准值',lambda b:b.get('status')=='ok' and b.get('route')=='comparison'
            and [(r['基准值'],r['比较值'],r['差值']) for r in b['result']['rows']]==[(left,right,right-left)])
        ask('查看待补问题',lambda b:b.get('pending_tasks')==[])
        ask(f'继续待补查询编号{identifier}',lambda b:b['result'].get('clarification_code')=='pending_reference_completed')
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8/backend'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==11 and all(row['passed'] for row in records) else 1


if __name__=='__main__':raise SystemExit(main())
