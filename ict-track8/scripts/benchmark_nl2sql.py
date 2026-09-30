"""可复现的结构化问数延迟基准；不修改生产数据库。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import SEED_ROWS, SCHEMA_SQL, initialize_database


def build_database(path: Path, rows: int) -> None:
    initialize_database(path, force=True)
    with sqlite3.connect(path) as connection:
        extra = []
        for index in range(len(SEED_ROWS), rows):
            source = SEED_ROWS[index % len(SEED_ROWS)]
            extra.append((f"SYN-{index:07d}", *source[1:]))
        connection.executemany(
            "INSERT INTO sales_orders (order_id, order_date, region, channel, product_category, product_name, quantity, unit_price, sales_amount, customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            extra,
        )


def benchmark(path: Path, iterations: int) -> dict[str, object]:
    engine = Nl2SqlEngine(path)
    questions = ("2025年各地区的销量", "按客户等级统计销售额", "2025年华东地区的销售额")
    samples: list[float] = []
    failures = 0
    for index in range(iterations):
        started = time.perf_counter()
        try:
            result = engine.answer(questions[index % len(questions)])
            if result.status != "ok":
                failures += 1
        except Exception:
            failures += 1
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "rows": None,
        "iterations": iterations,
        "failures": failures,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max_ms": round(max(samples), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.rows < len(SEED_ROWS) or args.rows > 100_000:
        raise SystemExit("--rows 必须在 12 到 100000 之间")
    database = Path(__file__).resolve().parents[1] / "data" / f"benchmark-{args.rows}.sqlite"
    build_database(database, args.rows)
    result = benchmark(database, args.iterations)
    result["rows"] = args.rows
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
