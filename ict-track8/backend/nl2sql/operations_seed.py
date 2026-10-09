"""Additional CC0 synthetic enterprise operations; deterministic and additive."""
from datetime import date, timedelta
from contextlib import closing
import sqlite3


SCHEMA = '''
CREATE TABLE IF NOT EXISTS suppliers (
 supplier_id TEXT PRIMARY KEY, supplier_name TEXT NOT NULL UNIQUE,
 supplier_category TEXT NOT NULL, city TEXT NOT NULL, supplier_rating REAL NOT NULL);
CREATE TABLE IF NOT EXISTS purchase_orders (
 purchase_id TEXT PRIMARY KEY, purchase_date TEXT NOT NULL,
 supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
 product_id TEXT NOT NULL REFERENCES products(product_id),
 region_id TEXT NOT NULL REFERENCES regions(region_id), region TEXT NOT NULL,
 purchase_status TEXT NOT NULL, purchase_quantity INTEGER NOT NULL,
 purchase_amount REAL NOT NULL, lead_time_days INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS shipment_records (
 shipment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES sales_orders(order_id),
 shipment_date TEXT NOT NULL, region TEXT NOT NULL, carrier TEXT NOT NULL,
 shipment_status TEXT NOT NULL, shipping_cost REAL NOT NULL,
 transit_days INTEGER, late_days INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payment_receipts (
 receipt_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES sales_orders(order_id),
 payment_date TEXT NOT NULL, region TEXT NOT NULL, payment_method TEXT NOT NULL,
 receipt_status TEXT NOT NULL, collected_amount REAL NOT NULL CHECK(collected_amount>=0));
CREATE TABLE IF NOT EXISTS operating_expenses (
 expense_id TEXT PRIMARY KEY, expense_date TEXT NOT NULL,
 region_id TEXT NOT NULL REFERENCES regions(region_id), region TEXT NOT NULL,
 department TEXT NOT NULL, expense_category TEXT NOT NULL,
 expense_amount REAL NOT NULL CHECK(expense_amount>=0), approval_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employees (
 employee_id TEXT PRIMARY KEY, employee_name TEXT NOT NULL,
 region_id TEXT NOT NULL REFERENCES regions(region_id), region TEXT NOT NULL,
 department TEXT NOT NULL, position TEXT NOT NULL, employment_type TEXT NOT NULL,
 hire_date TEXT NOT NULL, employee_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payroll_records (
 payroll_id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(employee_id),
 payroll_date TEXT NOT NULL, region TEXT NOT NULL, department TEXT NOT NULL,
 base_salary REAL NOT NULL, bonus_amount REAL NOT NULL,
 salary_amount REAL NOT NULL, overtime_hours REAL NOT NULL,
 UNIQUE(employee_id,payroll_date));
CREATE TABLE IF NOT EXISTS web_traffic_daily (
 traffic_id TEXT PRIMARY KEY, visit_date TEXT NOT NULL, region TEXT NOT NULL,
 traffic_source TEXT NOT NULL, device_type TEXT NOT NULL,
 page_views INTEGER NOT NULL, visits INTEGER NOT NULL, web_conversions INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_purchase_scope ON purchase_orders(purchase_date,region);
CREATE INDEX IF NOT EXISTS idx_shipment_scope ON shipment_records(shipment_date,region);
CREATE INDEX IF NOT EXISTS idx_receipt_scope ON payment_receipts(payment_date,region);
CREATE INDEX IF NOT EXISTS idx_expense_scope ON operating_expenses(expense_date,region);
CREATE INDEX IF NOT EXISTS idx_payroll_scope ON payroll_records(payroll_date,region);
CREATE INDEX IF NOT EXISTS idx_web_scope ON web_traffic_daily(visit_date,region);
'''


def initialize_operations_data(path):
    # Only called by the default demo initializer, never by configured business DBs.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        existing = db.execute("SELECT 1 FROM sqlite_master WHERE name='web_traffic_daily'").fetchone()
        if existing and db.execute("SELECT 1 FROM web_traffic_daily WHERE traffic_id='WEB-1825-7'").fetchone():
            return
        db.executescript(SCHEMA)
        regions = db.execute('SELECT region_id,region_name FROM regions ORDER BY region_id').fetchall()
        products = db.execute('SELECT product_id,unit_cost FROM products ORDER BY product_id').fetchall()
        suppliers = [(f'SUP-{i:03d}',f'{("恒信","远航","汇达","新科","瑞丰","中联")[i%6]}供应商{i:02d}',
            ('电子元件','成品供货','包装材料','物流服务')[i%4],('深圳','苏州','上海','成都','北京','武汉')[i%6],round(3.2+i%19/10,1)) for i in range(1,37)]
        db.executemany('INSERT OR IGNORE INTO suppliers VALUES (?,?,?,?,?)',suppliers)
        purchases=[]
        for i in range(1,3601):
            rid,region=regions[(i*7)%len(regions)];pid,cost=products[(i*11)%len(products)]
            quantity=20+(i*37)%380
            purchases.append((f'PUR-{i:05d}',(date(2022,1,1)+timedelta(days=(i*71)%1826)).isoformat(),
                suppliers[(i*13)%len(suppliers)][0],pid,rid,region,('已入库','已下单','运输中','已取消')[i%4],
                quantity,round(cost*quantity*(.92+i%13/100),2),2+(i*17)%45))
        db.executemany('INSERT OR IGNORE INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?)',purchases)
        orders=db.execute('SELECT order_id,order_date,region,sales_amount FROM sales_orders ORDER BY order_id').fetchall()
        shipments=[];receipts=[]
        for i,(oid,day,region,amount) in enumerate(orders):
            shipped=date.fromisoformat(day)+timedelta(days=1+i%4)
            status=('已签收','已签收','运输中','异常','已退回')[i%5]
            shipments.append((f'SHP-{oid}',oid,shipped.isoformat(),region,('顺丰','京东物流','中通','德邦')[i%4],
                status,round(8+i%120*.8,2),None if status=='运输中' else 1+i%8,0 if i%7 else 1+i%5))
            # Receipt is money actually collected; partial receipts never exceed the order amount.
            paid=round(amount*(.5 if i%9==0 else 1),2)
            receipts.append((f'PAY-{oid}',oid,(date.fromisoformat(day)+timedelta(days=i%30)).isoformat(),region,
                ('银行转账','微信支付','支付宝','信用卡')[i%4],'部分回款' if i%9==0 else '已到账',paid))
        db.executemany('INSERT OR IGNORE INTO shipment_records VALUES (?,?,?,?,?,?,?,?,?)',shipments)
        db.executemany('INSERT OR IGNORE INTO payment_receipts VALUES (?,?,?,?,?,?,?)',receipts)
        departments=('销售部','技术部','运营部','财务部','采购部','客服部')
        employees=[]
        for i in range(1,241):
            rid,region=regions[(i*5)%len(regions)]
            employees.append((f'EMP-{i:04d}',f'示例员工{i:03d}',rid,region,departments[i%6],
                ('专员','主管','经理','工程师')[i%4],('正式','合同','兼职')[i%3],
                (date(2020,1,1)+timedelta(days=i*3)).isoformat(),'在职'))
        db.executemany('INSERT OR IGNORE INTO employees VALUES (?,?,?,?,?,?,?,?,?)',employees)
        payroll=[];expenses=[]
        for month in range(60):
            month_text=f'{2022+month//12}-{1+month%12:02d}'
            for i,emp in enumerate(employees):
                base=4500+(i*137)%18000;bonus=round(base*(.05+(i+month)%18/100),2)
                payroll.append((f'SAL-{month:02d}-{emp[0]}',emp[0],month_text+'-01',emp[3],emp[4],base,bonus,base+bonus,float((i+month)%30)))
            for r,(rid,region) in enumerate(regions):
                for k,category in enumerate(('办公费','差旅费','租赁费','水电费','培训费','设备维护费')):
                    n=month*48+r*6+k
                    expenses.append((f'EXP-{n:05d}',month_text+f'-{1+n%27:02d}',rid,region,departments[(month+k+r)%6],
                        category,float(300+(n*7919)%48000),('已报销','已审批','待审批')[n%3]))
        db.executemany('INSERT OR IGNORE INTO payroll_records VALUES (?,?,?,?,?,?,?,?,?)',payroll)
        db.executemany('INSERT OR IGNORE INTO operating_expenses VALUES (?,?,?,?,?,?,?,?)',expenses)
        traffic=[]
        for day in range(1826):
            for r,(_,region) in enumerate(regions):
                visits=100+(day*313+r*7919)%9000;conversions=visits*(1+(day+r)%8)//100
                traffic.append((f'WEB-{day:04d}-{r}',(date(2022,1,1)+timedelta(days=day)).isoformat(),region,
                    ('自然搜索','付费搜索','社交媒体','直接访问','邮件营销')[(day+r)%5],
                    ('手机','电脑','平板')[(day+r)%3],visits*(2+(day+r)%5),visits,conversions))
        db.executemany('INSERT OR IGNORE INTO web_traffic_daily VALUES (?,?,?,?,?,?,?,?)',traffic)
