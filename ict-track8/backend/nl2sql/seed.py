"""构造可复现的赛题八示例业务数据库。"""

from __future__ import annotations

import sqlite3
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


def initialize_database(path: str | Path, *, force: bool = False) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if force and destination.exists():
        destination.unlink()
    with sqlite3.connect(destination) as connection:
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
