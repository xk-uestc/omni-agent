"""组合泛化生成集：槽位随机组合 -> (问题, 金标 SQL)。

目的：防止"为题库调规则"。题库里的每句话都可能被针对性修复，而这里的问题是
由指标 × 时间 × 取值(单值/多值/否定) × 维度 × 句式 随机组合出来的，金标 SQL
由同一组槽位的语义直接合成。生成器只描述语义，不访问被测系统。

固定随机种子保证可复现；换种子即可得到新的盲测集：
  python eval/generate_compositional.py --seed 7 --n 150
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

METRICS = [("销售额", "SUM(sales_amount)"), ("销量", "SUM(quantity)"), ("订单数", "COUNT(*)"), ("营业额", "SUM(sales_amount)")]
TIMES = [
    (None, None, None),
    ("2024年", "2024-01-01", "2025-01-01"),
    ("2025年", "2025-01-01", "2026-01-01"),
    ("2025年第二季度", "2025-04-01", "2025-07-01"),
    ("2025年下半年", "2025-07-01", "2026-01-01"),
    ("2025年4月", "2025-04-01", "2025-05-01"),
    ("2025年2月到5月", "2025-02-01", "2025-06-01"),
    ("2024年Q4", "2024-10-01", "2025-01-01"),
]
REGIONS = ["华东", "华南", "华北"]
CHANNELS = ["线上", "门店"]
CATEGORIES = ["手机", "平板", "耳机"]
DIMS = [(None, None), ("各地区", "region"), ("按渠道", "channel"), ("各品类", "product_category")]


def filter_slot(rng):
    kind = rng.choice(["none", "single", "multi", "negation"])
    column, values = rng.choice([("region", REGIONS), ("channel", CHANNELS), ("product_category", CATEGORIES)])
    if kind == "none":
        return "", None, None
    if kind == "single":
        value = rng.choice(values)
        return value, f"{column} = '{value}'", column
    if kind == "multi":
        a, b = rng.sample(values, 2)
        return f"{a}和{b}", f"{column} IN ('{a}', '{b}')", column
    value = rng.choice(values)
    return f"除{value}以外", f"{column} <> '{value}'", column


def generate(seed: int, n: int) -> list[dict]:
    rng = random.Random(seed)
    cases, seen = [], set()
    while len(cases) < n:
        metric_text, metric_sql = rng.choice(METRICS)
        time_text, start, end = rng.choice(TIMES)
        filter_text, filter_sql, filter_column = filter_slot(rng)
        dim_text, dim_column = rng.choice(DIMS)
        if dim_column and dim_column == filter_column:
            continue  # 同列既过滤又分组的组合语义不唯一，跳过
        parts = {"t": time_text or "", "f": filter_text, "d": dim_text or "", "m": metric_text}
        template = rng.choice(["{t}{f}{d}的{m}", "{f}{t}{d}{m}", "{d}{t}{f}的{m}", "{t}{d}{f}{m}是多少"])
        question = template.format(**parts)
        if question in seen:
            continue
        seen.add(question)
        where = [clause for clause in (f"order_date >= '{start}' AND order_date < '{end}'" if start else None, filter_sql) if clause]
        select = f"{dim_column}, {metric_sql}" if dim_column else metric_sql
        sql = f"SELECT {select} FROM sales_orders" + (" WHERE " + " AND ".join(where) if where else "")
        if dim_column:
            sql += " GROUP BY 1"
        cases.append({
            "id": f"G{seed}_{len(cases):03d}", "category": "generated", "db": "sales", "question": question,
            "expected": "ok", "gold_sql": sql, "tags": ["generated"], "variants": ["seed", "synthetic"],
            "order_sensitive": False, "safety": False,
        })
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--n", type=int, default=150)
    args = parser.parse_args()
    target = Path(__file__).resolve().parent / "cases" / f"generated_seed{args.seed}.json"
    target.write_text(json.dumps({"version": 2, "reference_date": "2026-09-22", "cases": generate(args.seed, args.n)},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.n} generated cases -> {target}")
