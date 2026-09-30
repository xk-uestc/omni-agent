"""可复现的行业级赛题八示例库。

与最小销售示例不同，这个数据包刻意包含订单、明细、产品、客户、区域和
售后工单等常见企业数据主题，验证规划器能否只依赖真实外键和别名配置迁移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS regions (
    region_id TEXT PRIMARY KEY,
    region_name TEXT NOT NULL,
    province TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_level TEXT NOT NULL,
    industry TEXT NOT NULL,
    region_id TEXT NOT NULL REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price REAL NOT NULL CHECK (unit_price >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    region_id TEXT NOT NULL REFERENCES regions(region_id)
);
CREATE TABLE IF NOT EXISTS order_items (
    item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    line_amount REAL NOT NULL CHECK (line_amount >= 0)
);
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    created_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    resolution_hours REAL CHECK (resolution_hours >= 0)
);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON support_tickets(created_date);
"""


REGION_ROWS = (
    ("R-E", "华东", "江苏"),
    ("R-S", "华南", "广东"),
    ("R-N", "华北", "北京"),
)

CUSTOMER_ROWS = (
    ("C-001", "东辰科技", "高", "制造业", "R-E"),
    ("C-002", "南方零售", "中", "零售业", "R-S"),
    ("C-003", "北极星教育", "高", "教育业", "R-N"),
    ("C-004", "华越传媒", "低", "传媒业", "R-E"),
    ("C-005", "海桥物流", "中", "物流业", "R-S"),
    ("C-006", "云杉医疗", "高", "医疗业", "R-N"),
)

PRODUCT_ROWS = (
    ("P-001", "星河 Pro", "手机", 3999.0),
    ("P-002", "星河 Lite", "手机", 2299.0),
    ("P-003", "云屏 11", "平板", 2499.0),
    ("P-004", "声场 Air", "耳机", 699.0),
    ("P-005", "智联 Hub", "配件", 499.0),
)

ORDER_ROWS = (
    ("O-001", "2025-01-07", "线上", "C-001", "R-E"),
    ("O-002", "2025-01-19", "门店", "C-002", "R-S"),
    ("O-003", "2025-02-04", "线上", "C-003", "R-N"),
    ("O-004", "2025-02-21", "门店", "C-004", "R-E"),
    ("O-005", "2025-03-06", "线上", "C-005", "R-S"),
    ("O-006", "2025-03-18", "线上", "C-006", "R-N"),
    ("O-007", "2025-04-22", "门店", "C-001", "R-E"),
    ("O-008", "2025-05-09", "线上", "C-002", "R-S"),
    ("O-009", "2025-06-11", "线上", "C-003", "R-N"),
)

ITEM_ROWS = (
    ("I-001", "O-001", "P-001", 2, 7998.0),
    ("I-002", "O-001", "P-004", 3, 2097.0),
    ("I-003", "O-002", "P-003", 2, 4998.0),
    ("I-004", "O-003", "P-002", 4, 9196.0),
    ("I-005", "O-003", "P-005", 1, 499.0),
    ("I-006", "O-004", "P-004", 5, 3495.0),
    ("I-007", "O-005", "P-001", 1, 3999.0),
    ("I-008", "O-005", "P-003", 2, 4998.0),
    ("I-009", "O-006", "P-001", 3, 11997.0),
    ("I-010", "O-007", "P-002", 2, 4598.0),
    ("I-011", "O-007", "P-005", 4, 1996.0),
    ("I-012", "O-008", "P-003", 1, 2499.0),
    ("I-013", "O-008", "P-004", 2, 1398.0),
    ("I-014", "O-009", "P-001", 1, 3999.0),
)

TICKET_ROWS = (
    ("T-001", "C-001", "2025-01-12", "已解决", "高", 6.5),
    ("T-002", "C-002", "2025-01-20", "处理中", "中", None),
    ("T-003", "C-003", "2025-02-08", "已解决", "高", 10.0),
    ("T-004", "C-004", "2025-02-26", "已关闭", "低", 24.0),
    ("T-005", "C-005", "2025-03-22", "已解决", "中", 8.0),
    ("T-006", "C-006", "2025-03-25", "已解决", "高", 4.0),
)


def initialize_industry_database(path: str | Path, *, force: bool = False) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if force and destination.exists():
        destination.unlink()
    with sqlite3.connect(destination) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executemany("INSERT OR IGNORE INTO regions VALUES (?, ?, ?)", REGION_ROWS)
        connection.executemany("INSERT OR IGNORE INTO customers VALUES (?, ?, ?, ?, ?)", CUSTOMER_ROWS)
        connection.executemany("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?)", PRODUCT_ROWS)
        connection.executemany("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?)", ORDER_ROWS)
        connection.executemany("INSERT OR IGNORE INTO order_items VALUES (?, ?, ?, ?, ?)", ITEM_ROWS)
        connection.executemany("INSERT OR IGNORE INTO support_tickets VALUES (?, ?, ?, ?, ?, ?)", TICKET_ROWS)
    return destination
