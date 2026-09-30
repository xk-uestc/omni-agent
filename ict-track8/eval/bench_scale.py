"""效率与伸缩性基准（对应决赛"系统效率设计与伸缩性证明"）。

  python eval/bench_scale.py --repo <ict-track8 目录> --out bench.json [--rows 1000 10000 100000] [--pages 10 100 500]

测量内容：
1. 结构化：每个数据规模下，典型问题的冷启动延迟（首问，含 Schema/取值索引构建）
   与热态 p50/p95；1/4/8 线程并发下的吞吐（QPS）。
2. 非结构化：10/100/500 页文字 PDF 的解析+质量评估耗时（pdf_ingest）。
所有时间为本机墙钟，报告中写明硬件与 Python 版本；结论只用于趋势对比，
不代表部署环境的绝对性能。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

QUESTIONS = [
    ("simple_total", "销售额是多少"),
    ("filter_time_value", "2025年华东地区的销售额"),
    ("group", "按渠道统计订单数"),
    ("join_dimension", "按客户等级统计销售额"),
    ("nested_having_avg", "销售额高于平均的产品"),
    ("comparison", "2025年各地区销售额同比"),
]


def load_backend(repo: Path):
    sys.path.insert(0, str(repo.resolve()))
    from backend.nl2sql.engine import Nl2SqlEngine  # noqa: WPS433
    from backend.nl2sql.seed import initialize_database  # noqa: WPS433
    return Nl2SqlEngine, initialize_database


def build_db(initialize_database, path: Path, rows: int) -> Path:
    initialize_database(path, force=True)
    rng = random.Random(rows)
    products = [("手机", "星河 Pro", 3799.0), ("手机", "星河 Lite", 2199.0), ("平板", "云屏 11", 2399.0), ("耳机", "声场 Air", 599.0)]
    batch = []
    with sqlite3.connect(path) as connection:
        existing = connection.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0]
        for index in range(max(0, rows - existing)):
            category, name, price = rng.choice(products)
            quantity = rng.randint(1, 9)
            day = date(2024, 1, 1) + timedelta(days=rng.randint(0, 730))
            batch.append((f"BN-{index:07d}", day.isoformat(), rng.choice(["华东", "华南", "华北"]), rng.choice(["线上", "门店"]),
                          category, name, quantity, price, price * quantity, f"C-00{rng.randint(1, 8)}"))
        connection.executemany("INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
    return path


def bench_structured(Engine, initialize_database, sizes, repeats, workdir) -> list[dict]:
    results = []
    for rows in sizes:
        path = build_db(initialize_database, workdir / f"bench-{rows}.sqlite", rows)
        size_mb = round(path.stat().st_size / 1_048_576, 2)
        engine = Engine(path)
        started = time.perf_counter()
        engine.answer(QUESTIONS[0][1])
        cold_ms = (time.perf_counter() - started) * 1000

        def safe_answer(question):
            try:
                return engine.answer(question).status
            except Exception as exc:  # noqa: BLE001  记录失败而不是中断基准
                return f"failed:{type(exc).__name__}"
        per_question = {}
        for key, question in QUESTIONS:
            samples = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                status = safe_answer(question)
                samples.append((time.perf_counter() - t0) * 1000)
            samples.sort()
            per_question[key] = {"status": status, "p50_ms": round(statistics.median(samples), 3),
                                 "p95_ms": round(samples[int(0.95 * (len(samples) - 1))], 3)}
        throughput = {}
        for workers in (1, 4, 8):
            jobs = [QUESTIONS[i % len(QUESTIONS)][1] for i in range(max(24, repeats * 4))]
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                statuses = list(pool.map(safe_answer, jobs))
            elapsed = time.perf_counter() - t0
            throughput[str(workers)] = {"qps": round(len(jobs) / elapsed, 2), "failed": sum(1 for x in statuses if x.startswith("failed"))}
        status_counts = {}
        for sample in per_question.values():
            status_counts[sample["status"]] = status_counts.get(sample["status"], 0) + 1
        failed_total = sum(item["failed"] for item in throughput.values())
        results.append({"rows": rows, "db_mb": size_mb, "cold_first_query_ms": round(cold_ms, 3),
                        "warm": per_question, "status_counts": status_counts,
                        "failed_total": failed_total, "throughput_qps_by_threads": throughput})
        print(f"rows={rows:>7} cold={cold_ms:8.1f}ms  " + "  ".join(f"{k}={v['p50_ms']:.1f}ms{'' if v['status'] in ('ok', 'clarification') else '(' + v['status'] + ')'}" for k, v in per_question.items()) + f"  qps={throughput}", flush=True)
    return results


def bench_pdf(repo: Path, pages_list, workdir) -> list[dict]:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
        from backend.pdf_ingest import PdfIngestor
    except Exception as exc:  # noqa: BLE001
        return [{"skipped": f"{type(exc).__name__}: {exc}"}]
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    results = []
    for pages in pages_list:
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        for page in range(1, pages + 1):
            pdf.setFont("STSong-Light", 11)
            y = 800
            pdf.drawString(60, y, f"第{page}章 经营分析")
            for line in range(30):
                y -= 22
                pdf.drawString(60, y, f"{page}.{line} 华东地区销售额同比增长，毛利率 = (收入-成本)/收入，收入 {1000 + line}，成本 {600 + line}。")
            pdf.showPage()
        pdf.save()
        data = buffer.getvalue()
        ingestor = PdfIngestor()
        t0 = time.perf_counter()
        report = ingestor.analyze(data, document_id=f"bench-{pages}.pdf")
        elapsed = (time.perf_counter() - t0) * 1000
        results.append({"pages": pages, "pdf_kb": round(len(data) / 1024, 1), "ingest_ms": round(elapsed, 1),
                        "ms_per_page": round(elapsed / pages, 2), "ocr_required_pages": len(report["pdf_metrics"]["ocr_required_pages"])})
        print(f"pages={pages:>4} ingest={elapsed:9.1f}ms  per_page={elapsed / pages:6.2f}ms", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--rows", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--pages", type=int, nargs="+", default=[10, 100, 500])
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fail-on-error", action="store_true", help="存在结构化查询失败时以非零退出")
    args = parser.parse_args()
    Engine, initialize_database = load_backend(args.repo)
    workdir = Path(tempfile.mkdtemp(prefix="ict8-bench-"))
    report = {
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
                        "sqlite": sqlite3.sqlite_version, "repo": str(args.repo)},
        "structured": bench_structured(Engine, initialize_database, args.rows, args.repeats, workdir),
        "pdf_ingest": bench_pdf(args.repo, args.pages, workdir),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    failed_total = sum(item.get("failed_total", 0) for item in report["structured"])
    if args.fail_on_error and failed_total:
        raise SystemExit(f"benchmark failed queries: {failed_total}")


if __name__ == "__main__":
    main()
