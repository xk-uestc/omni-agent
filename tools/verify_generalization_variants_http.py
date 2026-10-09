"""Different entities, scenarios and wording through the actual HTTP service.

This is an authored development check over synthetic demo data, not a public
benchmark. Numeric expectations are queried independently from SQLite.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from evaluate_authored_multiturn_http import numeric_values,document_ids


def main():
    url=os.getenv('ICT8_EVAL_BASE_URL','http://127.0.0.1:8031')
    db=sqlite3.connect((ROOT/'ict-track8/data/demo_sales.sqlite').as_uri()+'?mode=ro',uri=True)
    def base(year,region,metric='SUM(sales_amount)'):
        return db.execute(f'SELECT {metric} FROM sales_orders WHERE region=? AND order_date>=? AND order_date<?',
                          (region,f'{year}-01-01',f'{year+1}-01-01')).fetchone()[0]
    forecast_base=base(2025,'华北')
    cases=[
        ('sql','2024年华北销售额',[base(2024,'华北')],[]),
        ('sql','换成订单数，地区仍是华北，年份不变。',[base(2024,'华北','COUNT(order_id)')],[]),
        ('sql','年份改成2025，地区换到华东，指标还是订单数。',[base(2025,'华东','COUNT(order_id)')],[]),
        ('sql','销售额和订单数一起给出，地区、年份不变。',[base(2025,'华东'),base(2025,'华东','COUNT(order_id)')],[]),
        ('forecast','先查2025年华北销售额作预测基准，再结合《2026经营预测报告》的公式和《区域目标表》中华北2026目标增长率，算出华北2026目标销售额，并分别标明PDF、Excel和数据库来源。',[forecast_base*1.08],['forecast-report','region-targets']),
        ('forecast','地区和基准年份不变，换用预测报告的保守情景，重新计算华北目标。',[forecast_base*1.08],['forecast-report']),
        ('forecast','保守情景目标比区域目标表口径多多少金额？保持相同的2025年华北销售额基准。',[0],['forecast-report','region-targets']),
        ('forecast','总结华北2026目标额：区域目标表8%口径和报告保守情景8%口径各是多少？请注明两者都是预测目标，不能当作2026实际销售额。',[forecast_base*1.08],['forecast-report','region-targets']),
        ('reject','基准年份和地区不变，把增长率改用预测报告的保守情景9%，重算华北目标。',[],[]),
    ]
    sessions={name:'generalization-variant-'+uuid.uuid4().hex[:12] for name in ('sql','forecast')}
    records=[]
    for group,question,expected,docs in cases:
        started=time.perf_counter()
        result=requests.post(url+'/api/v1/omni/query',json={
            'question':question,'session_id':sessions['forecast' if group=='reject' else group]},timeout=(10,240))
        data=result.json()
        numbers=numeric_values(data)
        matches=all(any(abs(value-float(number))<=.02 for number in numbers) for value in expected)
        checks={'http_ok':result.status_code==200,
                'status_expected':data.get('status')==('clarification' if group=='reject' else 'ok'),
                'independent_values_match':matches,
                'source_documents':set(docs)<=set(document_ids(data))}
        record={'question':question,'checks':checks,'passed':all(checks.values()),
                'seconds':round(time.perf_counter()-started,3),'expected':expected,'response':data}
        records.append(record)
        print(json.dumps({key:record[key] for key in ('question','checks','passed','seconds')},ensure_ascii=False),flush=True)
        (ROOT/'runtime/generalization-variants-http.json').write_text(json.dumps({
            'scope':'自创合成演示数据变体检查，不是公开基准','base_url':url,'records':records,
            'passed':sum(r['passed'] for r in records),'executed':len(records)},ensure_ascii=False,indent=2),encoding='utf-8')
    db.close()
    return 0 if all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
