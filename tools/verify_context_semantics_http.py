"""Live demo regression: independently authored SQL, no credentials in output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def cases():
    # A single long session tests rolling history, then independent topics.
    rows = []
    def add(question, year, region, table, field, day, *, function='SUM', extra=''):
        expression = 'COUNT(*)' if function == 'COUNT' else f'{function}({field})'
        sql = f'SELECT {expression} FROM {table} WHERE {day}>=? AND {day}<? AND region=?'
        rows.append((question, sql + extra, (f'{year}-01-01', f'{year+1}-01-01', region)))
    for q, y, r, f, fn in [
        ('2025年华东销售额',2025,'华东','sales_amount','SUM'),
        ('那华南呢',2025,'华南','sales_amount','SUM'),
        ('再给我订单数，其他条件不变',2025,'华南','order_id','COUNT'),
        ('只看华北',2025,'华北','order_id','COUNT'),
        ('同样条件，换成2024年',2024,'华北','order_id','COUNT'),
        ('销量也查一下',2024,'华北','quantity','SUM'),
        ('华东也查一下',2024,'华东','quantity','SUM'),
        ('换成毛利',2024,'华东','gross_profit','SUM'),
        ('那2025年呢',2025,'华东','gross_profit','SUM'),
        ('销售额也查一下',2025,'华东','sales_amount','SUM'),
        ('同样条件，2024年，华南，订单数',2024,'华南','order_id','COUNT'),
        ('那华北呢',2024,'华北','order_id','COUNT'),
    ]:
        add(q,y,r,'sales_orders',f,'order_date',function=fn)
    domains = [
        ('2025年华东卖货赚了多少','sales_orders','gross_profit','order_date'),
        ('2025年华东卖出去多少件','sales_orders','quantity','order_date'),
        ('2025年华东营业额','sales_orders','sales_amount','order_date'),
        ('2025年华东采购花的钱','purchase_orders','purchase_amount','purchase_date'),
        ('2025年华东运货花的钱','shipment_records','shipping_cost','shipment_date'),
        ('2025年华东工资发了多少钱','payroll_records','salary_amount','payroll_date'),
        ('2025年华东回款到账金额','payment_receipts','collected_amount','payment_date'),
        ('2025年华东运营花了多少钱','operating_expenses','expense_amount','expense_date'),
        ('2025年华东网页被看了几次','web_traffic_daily','page_views','visit_date'),
        ('2025年华东网站来了多少次访问','web_traffic_daily','visits','visit_date'),
    ]
    for q,t,f,d in domains:
        add(q,2025,'华东',t,f,d)
        add('那华南呢',2025,'华南',t,f,d)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8031')
    parser.add_argument('--database',type=Path,default=ROOT/'ict-track8/data/demo_sales.sqlite')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        parser.error('Preserve existing evidence; choose a new output file')
    session='semantic-live-'+uuid.uuid4().hex[:16]
    records=[]
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for i,(question,sql,params) in enumerate(cases()):
            gold=db.execute(sql,params).fetchone()[0]
            started=time.perf_counter()
            try:
                request=urllib.request.Request(args.base_url.rstrip('/')+'/api/v1/omni/query',
                    data=json.dumps({'question':question,'session_id':session}).encode(),
                    headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(request,timeout=90) as response:
                    value=json.load(response)
                elapsed=(time.perf_counter()-started)*1000
                result=value.get('result',{})
                rows=result.get('rows',[])
                actual=list(rows[0].values())[0] if len(rows)==1 and len(rows[0])==1 else None
                correct=value.get('status')=='ok' and value.get('route')=='sql'
                correct=correct and ((actual is None and gold is None) or
                    isinstance(actual,(int,float)) and isinstance(gold,(int,float))
                    and abs(actual-gold)<=max(1e-5,abs(gold)*1e-9))
                correct=correct and value.get('question')==question and bool(result.get('sql'))
                records.append({'round':i+1,'question':question,'pass':bool(correct),
                    'gold_sql':sql,'gold_parameters':params,'expected':gold,'actual':actual,
                    'wall_ms':round(elapsed,3),'status':value.get('status'),
                    'effective_question':value.get('effective_question'),
                    'sql':result.get('sql'),'parameters':result.get('parameters'),
                    'planner_audit':result.get('plan',{}).get('planner_audit'),
                    'language_normalization':value.get('language_normalization'),
                    'clarification':result.get('clarification')})
            except Exception as exc:
                records.append({'round':i+1,'question':question,'pass':False,
                    'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None),
                    'wall_ms':round((time.perf_counter()-started)*1000,3)})
            print(json.dumps({'round':i+1,'pass':records[-1]['pass'],
                              'question':question},ensure_ascii=False),flush=True)
    report={'scope':'live_synthetic_demo_http_not_public_benchmark',
        'base_url':args.base_url,'total':len(records),
        'passed':sum(r['pass'] for r in records),'cases':records}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
