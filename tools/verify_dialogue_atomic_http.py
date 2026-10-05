"""Fresh continuous real HTTP scenarios, with independently computed SQL gold."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.seed import initialize_database


def main():
    session='atomic-dialogue-'+uuid.uuid4().hex
    sources=['backend/executed_scope_edit.py','backend/pending_scope_edit.py',
        'backend/sql_history_scope.py','backend/clarification.py','backend/clarification_continuation.py',
        'backend/pending_source_validation.py','backend/omni_agent.py']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in sources}
    reference=initialize_database(ROOT/'runtime'/f'{session}-reference.sqlite')
    with closing(sqlite3.connect(reference)) as con:
        def gold(year,region,metric='SUM(sales_amount)',channel=None):
            sql=f'SELECT {metric} FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?'
            params=[f'{year}-01-01',f'{year+1}-01-01',region]
            if channel:sql+=' AND channel=?';params.append(channel)
            return con.execute(sql,params).fetchone()[0]
        def monthly(year,region):
            return [{'月份':m,'销售额':v} for m,v in con.execute("SELECT strftime('%Y-%m',order_date),SUM(sales_amount) "
                "FROM sales_orders WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1",
                (f'{year}-01-01',f'{year+1}-01-01',region))]
        scenarios=[
            ('2025年华东销售额','ok','2025年华东销售额',[{'销售额':gold(2025,'华东')}],None),
            ('时间改成2024年，地区改成华南，指标改成订单数','ok','2024年华南订单数',[{'订单数':gold(2024,'华南','COUNT(order_id)')}],'server_verified_executed_sql_edit'),
            ('增加渠道筛选为线上','ok',None,[{'订单数':gold(2024,'华南','COUNT(order_id)','线上')}],'server_verified_executed_sql_edit'),
            ('时间改成2023年，地区改成火星','clarification',None,None,'executed_sql_edit_rejected'),
            ('地区改成华北','ok',None,[{'订单数':gold(2024,'华北','COUNT(order_id)','线上')}],'server_verified_executed_sql_edit'),
            ('删除渠道筛选','ok',None,[{'订单数':gold(2024,'华北','COUNT(order_id)')}],'server_verified_executed_sql_edit'),
            ('2025年华东销售额趋势','clarification','2025年华东销售额趋势',None,None),
            ('选第99个','clarification','2025年华东销售额趋势',None,'pending_sql_clarification_retained'),
            ('选项有什么区别','clarification','2025年华东销售额趋势',None,'pending_sql_clarification_retained'),
            ('选第二个','ok',None,[{'年份':'2025','销售额':gold(2025,'华东')}],'server_verified_sql_clarification_option_fill'),
            ('时间改成2024年，地区改成华南','ok',None,[{'年份':'2024','销售额':gold(2024,'华南')}],'server_verified_executed_sql_edit'),
            ('2025年华北销售额','ok','2025年华北销售额',[{'销售额':gold(2025,'华北')}],None),
            ('地区改成华南','ok','2025年华南销售额',[{'销售额':gold(2025,'华南')}],'server_verified_executed_sql_edit'),
            ('删除地区筛选，添加渠道筛选为线下，时间改成0000年','clarification','2025年华南销售额',None,'executed_sql_edit_rejected'),
            ('时间改成2024年','ok','2024年华南销售额',[{'销售额':gold(2024,'华南')}],'server_verified_executed_sql_edit'),
            ('2025年华东按月销售额趋势','ok',None,monthly(2025,'华东'),None),
            ('地区改成华南，时间改成2024年','ok',None,monthly(2024,'华南'),'server_verified_executed_sql_edit'),
        ]
        records=[]
        for question,status,scope,rows,mode in scenarios:
            request=Request('http://127.0.0.1:8030/api/v1/omni/query',
                data=json.dumps({'question':question,'session_id':session},ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json'})
            try:
                with urlopen(request,timeout=300) as response:data=json.load(response)
            except HTTPError as exc:
                records.append({'question':question,'passed':False,'http_status':exc.code})
                if exc.code in (401,403):break
                continue
            result=data.get('result',{});audit=data.get('context_resolution',{})
            passed=(data.get('status')==status and (scope is None or data.get('effective_question')==scope)
                and (rows is None or result.get('rows')==rows) and (mode is None or audit.get('mode')==mode)
                and (status!='clarification' or result.get('sql') is None))
            record={'question':question,'passed':passed,'status':data.get('status'),
                'effective_question':data.get('effective_question'),'planner_source':data.get('planner_source'),
                'context_resolution':audit,'expected_rows':rows,'result':result,'trace':data.get('trace',[])}
            records.append(record)
            print(json.dumps({k:record[k] for k in ('question','passed','status','effective_question')},ensure_ascii=False),flush=True)
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==h for name,h in hashes.items())
        output=ROOT/'runtime'/f'{session}-http.json'
        output.write_text(json.dumps({'not_official_benchmark':True,'model_allowed':'gpt-6-luna',
            'source_code_sha256':hashes,'source_stable':stable,'session_id':session,
            'planned_turns':len(scenarios),'passed':sum(r['passed'] for r in records),'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(output),flush=True)
        return 0 if stable and len(records)==len(scenarios) and all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
