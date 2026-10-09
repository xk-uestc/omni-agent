"""Live synthetic-demo questions, independently checked with read-only SQLite."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import sqlite3
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('2025年华东采购金额', 'SELECT SUM(purchase_amount) FROM purchase_orders WHERE purchase_date>=? AND purchase_date<? AND region=?', ('2025-01-01','2026-01-01','华东')),
    ('2025年已入库的采购数量', 'SELECT SUM(purchase_quantity) FROM purchase_orders WHERE purchase_date>=? AND purchase_date<? AND purchase_status=?', ('2025-01-01','2026-01-01','已入库')),
    ('2025年各承运商运费', 'SELECT carrier,SUM(shipping_cost) FROM shipment_records WHERE shipment_date>=? AND shipment_date<? GROUP BY carrier', ('2025-01-01','2026-01-01')),
    ('2025年华东回款金额', 'SELECT SUM(collected_amount) FROM payment_receipts WHERE payment_date>=? AND payment_date<? AND region=?', ('2025-01-01','2026-01-01','华东')),
    ('2025年各支付方式回款金额', 'SELECT payment_method,SUM(collected_amount) FROM payment_receipts WHERE payment_date>=? AND payment_date<? GROUP BY payment_method', ('2025-01-01','2026-01-01')),
    ('2025年各费用类别费用金额', 'SELECT expense_category,SUM(expense_amount) FROM operating_expenses WHERE expense_date>=? AND expense_date<? GROUP BY expense_category', ('2025-01-01','2026-01-01')),
    ('各部门员工数', 'SELECT department,COUNT(*) FROM employees GROUP BY department', ()),
    ('2025年各部门薪酬总额', 'SELECT department,SUM(salary_amount) FROM payroll_records WHERE payroll_date>=? AND payroll_date<? GROUP BY department', ('2025-01-01','2026-01-01')),
    ('2025年华南加班时长', 'SELECT SUM(overtime_hours) FROM payroll_records WHERE payroll_date>=? AND payroll_date<? AND region=?', ('2025-01-01','2026-01-01','华南')),
    ('2025年各流量来源网站访问量', 'SELECT traffic_source,SUM(visits) FROM web_traffic_daily WHERE visit_date>=? AND visit_date<? GROUP BY traffic_source', ('2025-01-01','2026-01-01')),
    ('2025年华东网站转化率', 'SELECT SUM(web_conversions)*1.0/SUM(visits) FROM web_traffic_daily WHERE visit_date>=? AND visit_date<? AND region=?', ('2025-01-01','2026-01-01','华东')),
    ('2025年华东地区的销售额', 'SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?', ('2025-01-01','2026-01-01','华东')),
]


def canonical(rows):
    return sorted([list(row.values()) if isinstance(row,dict) else list(row) for row in rows], key=lambda row:repr(row[:-1]))


def run(case):
    question,sql,params = case
    with sqlite3.connect(f'file:{ROOT / "ict-track8/data/demo_sales.sqlite"}?mode=ro',uri=True) as connection:
        expected = canonical(connection.execute(sql,params).fetchall())
    started=time.monotonic()
    record={'question':question,'oracle_sql':sql,'oracle_parameters':params,'expected':expected}
    try:
        session=requests.Session();session.trust_env=False
        response=session.post('https://raysource.cloud/demo/api/v1/omni/query',
            json={'question':question,'reset_context':True},timeout=180)
        response.raise_for_status();data=response.json();record['response']=data
        actual=canonical(data.get('result',{}).get('rows',[]));record['actual']=actual
        record['passed']=(data.get('status')=='ok' and data.get('route')=='sql' and len(actual)==len(expected)
            and all(len(a)==len(b) and all(math.isclose(x,y,rel_tol=1e-9,abs_tol=.005) if isinstance(y,(int,float))
                and isinstance(x,(int,float)) else x==y for x,y in zip(a,b)) for a,b in zip(actual,expected)))
    except Exception as exc:record.update(passed=False,error=str(exc))
    record['seconds']=round(time.monotonic()-started,3)
    print(json.dumps({key:record[key] for key in ('question','passed','seconds')},ensure_ascii=False),flush=True)
    return record


if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(run,CASES))
    report={'passed':sum(record['passed'] for record in records),'total':len(records),'records':records}
    (ROOT/'runtime/operations-demo-live-20261007.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('passed','total')}),flush=True)
    raise SystemExit(0 if report['passed']==report['total'] else 1)
