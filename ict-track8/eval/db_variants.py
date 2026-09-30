"""评测用数据库变体（test-suite 思路：同一金标 SQL 在多个库上比对执行结果）。

参考 Zhong, Yu & Klein, "Semantic Evaluation for Text-to-SQL with Distilled
Test Suites" (EMNLP 2020)：只在一个小种子库上比较结果，很容易让错误 SQL
"碰巧正确"（上一轮审查中扇出计数、日期列错绑都属于这种情况）。因此每道题
在以下变体上分别执行：

- seed       项目自带种子库（与现有测试一致）
- synthetic  同 Schema、固定随机种子生成的中等规模数据（覆盖更多取值组合）
- mutated    针对性扰动：一对多扇出、订单地区≠客户地区、非下单月工单、
             并列名次、NULL 指标——专门让"碰巧正确"的错误暴露出来

本模块只依赖被评测仓库的 seed 函数与 sqlite3，自身不含任何答案。
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SALES_VARIANTS = ("seed", "synthetic", "mutated")
INDUSTRY_VARIANTS = ("seed", "synthetic", "mutated")


def build_sales(repo_backend, variant: str, path: Path) -> Path:
    seed = repo_backend.nl2sql.seed
    path = Path(path)
    seed.initialize_database(path, force=True)
    if variant == "seed":
        return path
    with sqlite3.connect(path) as connection:
        if variant == "mutated":
            connection.executemany(
                "INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    # 让"云屏 11"销量与"星河 Pro"并列第 2（检验 Top-N 并列策略）
                    ("MU-001", "2025-09-03", "华北", "线上", "平板", "云屏 11", 2, 2399.0, 4798.0, "C-003"),
                    # 边界日：2025-12-31 与 2026-01-01（检验半开区间）
                    ("MU-002", "2025-12-31", "华东", "门店", "耳机", "声场 Air", 1, 599.0, 599.0, "C-004"),
                    ("MU-003", "2026-01-01", "华东", "门店", "耳机", "声场 Air", 1, 599.0, 599.0, "C-004"),
                ],
            )
        elif variant == "synthetic":
            rng = random.Random(20260922)
            products = [("手机", "星河 Pro", 3799.0), ("手机", "星河 Lite", 2199.0), ("平板", "云屏 11", 2399.0), ("耳机", "声场 Air", 599.0)]
            regions, channels = ["华东", "华南", "华北"], ["线上", "门店"]
            customers = [f"C-00{i}" for i in range(1, 9)]
            rows = []
            start = date(2024, 1, 1)
            for index in range(600):
                category, name, price = rng.choice(products)
                quantity = rng.randint(1, 9)
                day = start + timedelta(days=rng.randint(0, 730))
                rows.append((f"SY-{index:05d}", day.isoformat(), rng.choice(regions), rng.choice(channels), category, name,
                             quantity, price, round(price * quantity, 2), rng.choice(customers)))
            connection.executemany("INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        else:
            raise ValueError(f"unknown sales variant {variant}")
    return path


def build_industry(repo_backend, variant: str, path: Path) -> Path:
    seed = repo_backend.nl2sql.industry_seed
    path = Path(path)
    seed.initialize_industry_database(path, force=True)
    if variant == "seed":
        return path
    with sqlite3.connect(path) as connection:
        if variant == "mutated":
            # 1) 一对多扇出：O-001 再加一条手机明细 -> "按品类统计订单数" 的手机应仍是去重订单数
            connection.execute("INSERT INTO order_items VALUES ('MU-I1', 'O-001', 'P-002', 1, 2299.0)")
            # 2) 非下单月的工单：C-001 在 3 月提工单，但 3 月没有下单 -> 检验时间列绑定
            connection.execute("INSERT INTO support_tickets VALUES ('MU-T1', 'C-001', '2025-03-10', '已解决', '低', 30.0)")
            # 3) 订单地区 ≠ 客户地区：检验"客户所在地区"与默认订单地区两种口径
            connection.execute("INSERT INTO orders VALUES ('MU-O1', '2025-06-20', '线上', 'C-001', 'R-S')")
            connection.execute("INSERT INTO order_items VALUES ('MU-I2', 'MU-O1', 'P-003', 2, 4998.0)")
            # 4) NULL 指标
            connection.execute("INSERT INTO support_tickets VALUES ('MU-T2', 'C-003', '2025-06-02', '处理中', '中', NULL)")
        elif variant == "synthetic":
            rng = random.Random(8014)
            regions = ["R-E", "R-S", "R-N"]
            customers = [f"C-00{i}" for i in range(1, 7)]
            products = [("P-001", 3999.0), ("P-002", 2299.0), ("P-003", 2499.0), ("P-004", 699.0), ("P-005", 499.0)]
            item_index = 0
            for index in range(300):
                order_id = f"SY-O{index:04d}"
                day = date(2024, 1, 1) + timedelta(days=rng.randint(0, 730))
                connection.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                                   (order_id, day.isoformat(), rng.choice(["线上", "门店"]), rng.choice(customers), rng.choice(regions)))
                for _ in range(rng.randint(1, 3)):
                    product, price = rng.choice(products)
                    quantity = rng.randint(1, 5)
                    connection.execute("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
                                       (f"SY-I{item_index:05d}", order_id, product, quantity, price * quantity))
                    item_index += 1
            for index in range(120):
                day = date(2024, 1, 1) + timedelta(days=rng.randint(0, 730))
                status = rng.choice(["已解决", "已关闭", "处理中"])
                hours = None if status == "处理中" else round(rng.uniform(1, 48), 1)
                connection.execute("INSERT INTO support_tickets VALUES (?, ?, ?, ?, ?, ?)",
                                   (f"SY-T{index:04d}", rng.choice(customers), day.isoformat(), status, rng.choice(["高", "中", "低"]), hours))
        else:
            raise ValueError(f"unknown industry variant {variant}")
    return path
