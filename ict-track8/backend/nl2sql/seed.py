"""构造可复现的赛题八示例业务数据库。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sales_orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    product_category TEXT NOT NULL,
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    sales_amount REAL NOT NULL CHECK (sales_amount >= 0),
    customer_id TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_level TEXT NOT NULL,
    industry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_orders_date ON sales_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_sales_orders_region ON sales_orders(region);
"""

SEED_ROWS = (
    ("SO-001", "2024-01-15", "华东", "线上", "手机", "星河 Pro", 3, 3999.0, 11997.0, "C-001"),
    ("SO-002", "2024-02-04", "华南", "门店", "平板", "云屏 11", 2, 2499.0, 4998.0, "C-002"),
    ("SO-003", "2024-03-21", "华北", "线上", "手机", "星河 Pro", 1, 3999.0, 3999.0, "C-003"),
    ("SO-004", "2024-05-13", "华东", "门店", "耳机", "声场 Air", 5, 699.0, 3495.0, "C-004"),
    ("SO-005", "2025-01-07", "华东", "线上", "手机", "星河 Pro", 4, 3799.0, 15196.0, "C-001"),
    ("SO-006", "2025-02-19", "华南", "线上", "平板", "云屏 11", 3, 2399.0, 7197.0, "C-002"),
    ("SO-007", "2025-03-06", "华北", "门店", "手机", "星河 Lite", 6, 2299.0, 13794.0, "C-003"),
    ("SO-008", "2025-04-22", "华东", "门店", "耳机", "声场 Air", 8, 599.0, 4792.0, "C-004"),
    ("SO-009", "2025-05-30", "华南", "线上", "手机", "星河 Lite", 2, 2199.0, 4398.0, "C-005"),
    ("SO-010", "2025-06-11", "华北", "线上", "耳机", "声场 Air", 7, 599.0, 4193.0, "C-006"),
    ("SO-011", "2025-07-16", "华东", "线上", "平板", "云屏 11", 4, 2399.0, 9596.0, "C-007"),
    ("SO-012", "2025-08-09", "华南", "门店", "手机", "星河 Pro", 3, 3799.0, 11397.0, "C-008"),
)

CUSTOMER_ROWS = (
    ("C-001", "东辰科技", "高", "制造业"),
    ("C-002", "南方零售", "中", "零售业"),
    ("C-003", "北极星教育", "高", "教育业"),
    ("C-004", "华越传媒", "低", "传媒业"),
    ("C-005", "海桥物流", "中", "物流业"),
    ("C-006", "云杉医疗", "高", "医疗业"),
    ("C-007", "星港咨询", "中", "咨询业"),
    ("C-008", "岭南餐饮", "低", "餐饮业"),
)

REGIONS = (
    ("R-E", "华东", "上海"), ("R-S", "华南", "广州"),
    ("R-N", "华北", "北京"), ("R-C", "华中", "武汉"),
    ("R-SW", "西南", "成都"), ("R-NW", "西北", "西安"),
    ("R-NE", "东北", "沈阳"), ("R-SE", "东南", "厦门"),
)
PRODUCT_CATALOG = (
    ("手机", "星河", (1299, 2299, 3799, 5299, 6999)),
    ("平板", "云屏", (1599, 2399, 3299, 4599, 6299)),
    ("耳机", "声场", (199, 399, 699, 1099, 1599)),
    ("电脑", "远山", (3999, 5299, 6999, 8999, 11999)),
    ("智能家居", "智家", (299, 599, 999, 1699, 2699)),
    ("穿戴设备", "星环", (399, 799, 1299, 1899, 2599)),
    ("影像设备", "光影", (1599, 2899, 4699, 6999, 9999)),
    ("配件", "灵犀", (49, 99, 199, 399, 799)),
)
CHANNELS = ("线上商城", "直营网店", "经销商", "企业直销", "直播电商")
INDUSTRIES = ("制造业", "零售业", "教育业", "传媒业", "物流业", "医疗业",
              "咨询服务", "餐饮业", "金融业", "软件信息", "建筑业", "公共服务")
STATUSES = ("已完成", "已发货", "处理中", "已取消")
PAYMENT_METHODS = ("银行卡", "移动支付", "企业转账", "信用支付", "分期付款")
TICKET_TYPES = ("产品咨询", "物流配送", "质量问题", "退换货", "账号服务", "发票开具", "售后维修")


def _rich_date(offset: int) -> str:
    return (date(2022, 1, 1) + timedelta(days=offset % 1826)).isoformat()


def initialize_rich_demo_data(path: str | Path) -> Path:
    """Expand only the public demo DB with deterministic, idempotent synthetic data."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        initialize_database(destination)

    products = [
        ("P-LEGACY-001", "星河 Pro", "手机", "星河", 3999.0, 2860.0, "2022-01-01"),
        ("P-LEGACY-002", "星河 Lite", "手机", "星河", 2299.0, 1550.0, "2022-01-01"),
        ("P-LEGACY-003", "云屏 11", "平板", "云屏", 2499.0, 1710.0, "2022-01-01"),
        ("P-LEGACY-004", "声场 Air", "耳机", "声场", 699.0, 330.0, "2022-01-01"),
    ]
    for category, brand, prices in PRODUCT_CATALOG:
        for tier, price in enumerate(prices, 1):
            suffix = ("标准版", "增强版", "轻享版", "专业版", "旗舰版")[tier - 1]
            product_id = f"P-{len(products) + 1:03d}"
            products.append((product_id, f"{brand} {suffix}", category, brand,
                             float(price), round(price * (0.52 + tier * 0.025), 2),
                             _rich_date((len(products) * 47) % 1826)))

    with closing(sqlite3.connect(destination)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS regions (
                region_id TEXT PRIMARY KEY, region_name TEXT NOT NULL UNIQUE,
                city TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY, product_name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL, brand TEXT NOT NULL,
                list_price REAL NOT NULL, unit_cost REAL NOT NULL,
                launch_date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sales_reps (
                sales_rep_id TEXT PRIMARY KEY, sales_rep_name TEXT NOT NULL,
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                team TEXT NOT NULL, hire_date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                assigned_rep_id TEXT NOT NULL REFERENCES sales_reps(sales_rep_id),
                created_date TEXT NOT NULL, status TEXT NOT NULL,
                priority TEXT NOT NULL, issue_type TEXT NOT NULL,
                resolution_hours REAL, satisfaction_score REAL,
                first_response_minutes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory_snapshots (
                snapshot_month TEXT NOT NULL,
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                product_id TEXT NOT NULL REFERENCES products(product_id),
                on_hand_quantity INTEGER NOT NULL,
                inbound_quantity INTEGER NOT NULL,
                safety_stock INTEGER NOT NULL,
                inventory_value REAL NOT NULL,
                PRIMARY KEY (snapshot_month, region_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS marketing_campaigns (
                campaign_id TEXT PRIMARY KEY, campaign_name TEXT NOT NULL,
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                channel TEXT NOT NULL, campaign_type TEXT NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                budget REAL NOT NULL, impressions INTEGER NOT NULL,
                clicks INTEGER NOT NULL, conversions INTEGER NOT NULL,
                attributed_sales REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sales_targets (
                target_id TEXT PRIMARY KEY, target_month TEXT NOT NULL,
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                target_sales REAL NOT NULL, target_orders INTEGER NOT NULL,
                UNIQUE (target_month, region_id)
            );
            CREATE TABLE IF NOT EXISTS sales_returns (
                return_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES sales_orders(order_id),
                customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                return_date TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL, refund_amount REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_date_status ON support_tickets(created_date, status);
            CREATE INDEX IF NOT EXISTS idx_inventory_month_region ON inventory_snapshots(snapshot_month, region_id);
            CREATE INDEX IF NOT EXISTS idx_campaign_date_region ON marketing_campaigns(start_date, region_id);
        """)
        connection.executemany("INSERT OR IGNORE INTO regions VALUES (?, ?, ?)", REGIONS)
        connection.executemany("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", products)

        for column, declaration in (
            ("region_id", "TEXT REFERENCES regions(region_id)"),
            ("product_id", "TEXT REFERENCES products(product_id)"),
            ("discount_amount", "REAL NOT NULL DEFAULT 0"),
            ("gross_profit", "REAL NOT NULL DEFAULT 0"),
            ("order_status", "TEXT NOT NULL DEFAULT '已完成'"),
            ("payment_method", "TEXT NOT NULL DEFAULT '移动支付'"),
            ("delivery_days", "INTEGER NOT NULL DEFAULT 3"),
            ("sales_rep_id", "TEXT REFERENCES sales_reps(sales_rep_id)"),
        ):
            existing = {row[1] for row in connection.execute("PRAGMA table_info(sales_orders)")}
            if column not in existing:
                connection.execute(f'ALTER TABLE sales_orders ADD COLUMN "{column}" {declaration}')
        for column, declaration in (
            ("region_id", "TEXT REFERENCES regions(region_id)"),
            ("acquisition_channel", "TEXT NOT NULL DEFAULT '线上商城'"),
            ("created_date", "TEXT NOT NULL DEFAULT '2022-01-01'"),
            ("annual_value", "REAL NOT NULL DEFAULT 0"),
        ):
            existing = {row[1] for row in connection.execute("PRAGMA table_info(customers)")}
            if column not in existing:
                connection.execute(f'ALTER TABLE customers ADD COLUMN "{column}" {declaration}')
        connection.execute("CREATE INDEX IF NOT EXISTS idx_rich_orders_region_date ON sales_orders(region_id, order_date)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_rich_orders_product ON sales_orders(product_id)")

        reps = [(f"REP-{i + 1:03d}", f"顾问{i + 1:03d}", REGIONS[i % len(REGIONS)][0],
                 ("华东一组", "华南二组", "行业大客户组", "线上增长组")[i % 4], _rich_date(i * 19))
                for i in range(48)]
        connection.executemany("INSERT OR IGNORE INTO sales_reps VALUES (?, ?, ?, ?, ?)", reps)

        generated_customers = []
        for i in range(1, 241):
            index = i - 1
            region_id = REGIONS[(index * 5 + 2) % len(REGIONS)][0]
            generated_customers.append((f"C-DEMO-{i:04d}", f"演示客户{i:04d}",
                ("普通", "成长", "重点", "战略")[index % 4], INDUSTRIES[(index * 7) % len(INDUSTRIES)],
                region_id, CHANNELS[(index * 3) % len(CHANNELS)], _rich_date(index * 11),
                float(8000 + (index * 7919) % 2_400_000)))
        for customer_id, name, level, industry, region_id, channel, created, annual in generated_customers:
            connection.execute("""INSERT OR IGNORE INTO customers
                (customer_id,customer_name,customer_level,industry,region_id,acquisition_channel,created_date,annual_value)
                VALUES (?,?,?,?,?,?,?,?)""", (customer_id,name,level,industry,region_id,channel,created,annual))

        region_ids = [item[0] for item in REGIONS]
        region_by_name = {name: code for code, name, _ in REGIONS}
        product_by_name = {item[1]: item for item in products}
        legacy_regions = {"华东": "R-E", "华南": "R-S", "华北": "R-N"}
        for customer_id, name, *_ in CUSTOMER_ROWS:
            region_id = legacy_regions["华东" if customer_id in {"C-001", "C-004", "C-007"}
                                       else "华南" if customer_id in {"C-002", "C-005", "C-008"} else "华北"]
            connection.execute("""UPDATE customers SET region_id=?, acquisition_channel='直营网店',
                created_date='2022-01-01', annual_value=? WHERE customer_id=?""",
                (region_id, float(25000 + int(customer_id[-3:]) * 9700), customer_id))

        generated_orders = []
        for i in range(1, 12_001):
            seed = i * 104729 + 97
            customer_index = (i * 37 + 11) % len(generated_customers)
            customer = generated_customers[customer_index]
            region_id = customer[4]
            region_name = next(name for code, name, _ in REGIONS if code == region_id)
            product = products[(i * 17 + 3) % len(products)]
            quantity = 1 + seed % 12
            unit_price = product[4] * (0.92 + (seed % 17) / 100)
            discount = round(unit_price * quantity * ((seed // 7) % 16) / 100, 2)
            sales_amount = round(unit_price * quantity - discount, 2)
            cost = product[5] * quantity
            generated_orders.append((f"SO-DEMO-{i:05d}", _rich_date(i * 137), region_name,
                CHANNELS[(seed // 13) % len(CHANNELS)], product[2], product[1], quantity,
                round(unit_price, 2), sales_amount, customer[0], region_id, product[0], discount,
                round(sales_amount - cost, 2), STATUSES[(seed // 19) % len(STATUSES)],
                PAYMENT_METHODS[(seed // 23) % len(PAYMENT_METHODS)], 1 + (seed // 29) % 14,
                f"REP-{((seed // 31) % 48) + 1:03d}"))
        connection.executemany("""INSERT OR IGNORE INTO sales_orders
            (order_id,order_date,region,channel,product_category,product_name,quantity,unit_price,sales_amount,
             customer_id,region_id,product_id,discount_amount,gross_profit,order_status,payment_method,delivery_days,sales_rep_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", generated_orders)

        for order_id, order_date, old_region, _channel, category, name, quantity, unit_price, amount, customer_id in SEED_ROWS:
            product = product_by_name.get(name)
            if product:
                seed = int(order_id[-3:])
                region_id = region_by_name[old_region]
                connection.execute("""UPDATE sales_orders SET region_id=?,product_id=?,discount_amount=?,gross_profit=?,
                    order_status='已完成',payment_method=?,delivery_days=?,sales_rep_id=? WHERE order_id=?""",
                    (region_id, product[0], round(quantity * unit_price - amount, 2),
                     round(amount - product[5] * quantity, 2), PAYMENT_METHODS[seed % len(PAYMENT_METHODS)],
                     2 + seed % 7, f"REP-{seed % 48 + 1:03d}", order_id))

        tickets = []
        for i in range(1, 4001):
            seed = i * 65537 + 313
            customer = generated_customers[(i * 29 + 7) % len(generated_customers)]
            status = ("已解决", "已解决", "处理中", "已关闭", "待受理")[(seed // 11) % 5]
            resolution = None if status in {"处理中", "待受理"} else round(1 + (seed % 720) / 10, 1)
            satisfaction = None if resolution is None else round(2.5 + (seed % 26) / 10, 1)
            tickets.append((f"T-DEMO-{i:05d}", customer[0], customer[4], f"REP-{(seed % 48) + 1:03d}",
                _rich_date(i * 31), status, ("低", "中", "高", "紧急")[(seed // 17) % 4],
                TICKET_TYPES[(seed // 23) % len(TICKET_TYPES)], resolution, satisfaction, 2 + seed % 1440))
        connection.executemany("INSERT OR IGNORE INTO support_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?)", tickets)

        targets, campaigns = [], []
        for month_index in range(60):
            year, month = 2022 + month_index // 12, month_index % 12 + 1
            month_start = date(year, month, 1)
            month_text = month_start.strftime("%Y-%m")
            for region_index, (region_id, region_name, _) in enumerate(REGIONS):
                base = 380_000 + region_index * 51_000 + month_index * 2_250
                targets.append((f"TARGET-{year}-{month:02d}-{region_id}", month_text, region_id,
                                float(base), 180 + region_index * 17 + month_index % 29))
                seed = month_index * 83 + region_index * 29
                budget = float(18_000 + (seed * 7919) % 210_000)
                impressions = 80_000 + (seed * 1877) % 2_400_000
                clicks = impressions * (2 + seed % 7) // 100
                conversions = clicks * (1 + (seed // 3) % 12) // 100
                campaigns.append((f"CMP-{year}-{month:02d}-{region_id}",
                    f"{region_name}{month}月{('新品推广','会员运营','品牌活动','直播转化')[month_index % 4]}",
                    region_id, CHANNELS[(month_index + region_index) % len(CHANNELS)],
                    ("新品上市", "会员复购", "节日促销", "内容种草")[month_index % 4],
                    month_start.isoformat(), date(year, month, min(28, 27 + month_index % 2)).isoformat(),
                    budget, impressions, clicks, conversions,
                    round(budget * (2.1 + (seed % 190) / 100), 2)))
        connection.executemany("INSERT OR IGNORE INTO sales_targets VALUES (?,?,?,?,?)", targets)
        connection.executemany("INSERT OR IGNORE INTO marketing_campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", campaigns)

        snapshots = []
        for month_index in range(60):
            month_text = date(2022 + month_index // 12, month_index % 12 + 1, 1).strftime("%Y-%m")
            for region_index, (region_id, _region_name, _) in enumerate(REGIONS):
                for product_index, product in enumerate(products):
                    seed = month_index * 7919 + region_index * 1543 + product_index * 313
                    stock = 30 + seed % 1400
                    inbound = (seed // 7) % 600
                    safety = 20 + (seed // 11) % 220
                    snapshots.append((month_text, region_id, product[0], stock, inbound, safety,
                                      round(stock * product[5], 2)))
        connection.executemany("INSERT OR IGNORE INTO inventory_snapshots VALUES (?,?,?,?,?,?,?)", snapshots)

        returns = []
        for i in range(1, 1201):
            order = generated_orders[(i * 43 + 19) % len(generated_orders)]
            seed = i * 8191 + 23
            purchased = date.fromisoformat(order[1])
            returned = min(date(2026, 12, 31), purchased + timedelta(days=1 + seed % 45))
            returns.append((f"RET-DEMO-{i:05d}", order[0], order[9], order[10],
                returned.isoformat(),
                ("规格不符", "质量问题", "物流破损", "重复下单", "其他")[seed % 5],
                ("已退款", "处理中", "已拒绝", "已换货")[(seed // 7) % 4],
                round(order[8] * (0.25 + (seed % 76) / 100), 2)))
        connection.executemany("""INSERT INTO sales_returns VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(return_id) DO UPDATE SET order_id=excluded.order_id,
            customer_id=excluded.customer_id,region_id=excluded.region_id,
            return_date=excluded.return_date,reason=excluded.reason,
            status=excluded.status,refund_amount=excluded.refund_amount""", returns)

    from .operations_seed import initialize_operations_data
    initialize_operations_data(destination)
    return destination


def initialize_database(path: str | Path, *, force: bool = False) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if force and destination.exists():
        destination.unlink()
    with closing(sqlite3.connect(destination)) as connection, connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executemany(
            "INSERT OR IGNORE INTO customers (customer_id, customer_name, customer_level, industry) VALUES (?, ?, ?, ?)",
            CUSTOMER_ROWS,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO sales_orders "
            "(order_id, order_date, region, channel, product_category, product_name, quantity, unit_price, sales_amount, customer_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            SEED_ROWS,
        )
    return destination
