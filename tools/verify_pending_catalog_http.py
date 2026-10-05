"""Live multi-task discovery/resume; actual SQL independently checked."""
import hashlib
import json
from pathlib import Path
import sys
import uuid
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from verify_pending_task_resume_http import reference


def main():
    session='pending-catalog-'+uuid.uuid4().hex
    names=['backend/omni_agent.py','backend/pending_task_catalog.py','backend/pending_task_store.py',
           'backend/pending_task_resume.py','backend/pending_source_validation.py','backend/session.py',
           'frontend/app.js','frontend/conversation-context.js','frontend/index.html']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    records=[];report=ROOT/'runtime'/f'pending-catalog-http-{uuid.uuid4().hex}.json'

    def send(question,check,*,foreign=False):
        request=Request('http://127.0.0.1:8030/api/v1/omni/query',data=json.dumps({
            'question':question,'session_id':('foreign-' if foreign else '')+session},ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:body=json.load(response)
        passed=check(body)
        records.append({'question':question,'foreign_session':foreign,'passed':passed,'response':body})
        print(json.dumps({'question':question,'passed':passed,'route':body.get('route')},ensure_ascii=False),flush=True)
        if not passed:raise AssertionError('Actual response failed verification; retained in report')
        return body

    pending=lambda body:body.get('status')=='clarification' and body.get('result',{}).get('clarification_code')=='missing_time_grain'
    catalog=lambda body:body.get('route')=='tasks' and body.get('status')=='ok' and 'query_reference_id' not in body
    try:
        first=send('2025年华东销售额趋势',pending)
        edited=send('时间改成2024年',lambda body:pending(body) and body.get('effective_question')=='2024年华东销售额趋势')
        second=send('2025年华南订单数趋势',pending)
        listing=send('查看待补问题',lambda body:catalog(body) and len(body.get('pending_tasks',[]))==2)
        identities={item['query_reference_id']:item for item in listing['pending_tasks']}
        assert first['query_reference_id'] not in identities
        assert identities[edited['query_reference_id']]['question']=='2024年华东销售额趋势'
        assert identities[second['query_reference_id']]['available'] is True
        send('继续没完成的问题',lambda body:catalog(body) and body.get('pending_tasks')==listing['pending_tasks'])
        send('查看待补问题',lambda body:catalog(body) and body.get('pending_tasks')==[],foreign=True)
        count=reference("SELECT strftime('%Y-%m',order_date) AS 月份,COUNT(order_id) AS 订单数 FROM sales_orders "
                        'WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',
                        ('2025-01-01','2026-01-01','华南'))
        send('按月统计',lambda body:body.get('status')=='ok' and body.get('result',{}).get('rows')==count)
        send('查看待补问题',lambda body:catalog(body) and [t['query_reference_id'] for t in body.get('pending_tasks',[])]==[edited['query_reference_id']])
        sales=reference("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 FROM sales_orders "
                        'WHERE order_date>=? AND order_date<? AND region=? AND channel=? GROUP BY 1 ORDER BY 1',
                        ('2024-01-01','2025-01-01','华东','线上'))
        send(f"继续待补查询编号{edited['query_reference_id']}，增加渠道筛选为线上，按月统计",
             lambda body:body.get('status')=='ok' and body.get('result',{}).get('rows')==sales)
        send('查看待补问题',lambda body:catalog(body) and body.get('pending_tasks')==[])
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,
            'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==10 and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
