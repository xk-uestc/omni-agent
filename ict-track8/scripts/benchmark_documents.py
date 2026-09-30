"""Benchmark deterministic document analysis at representative page counts."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.document_analysis import DocumentAnalyzer, PageSignal


def _pages(count: int) -> tuple[PageSignal, ...]:
    return tuple(
        PageSignal(
            page_no=index,
            text=(
                f"第 {index} 页 销售运营规范\n"
                "销售额按已完成订单统计。毛利率 = (销售额 - 成本) / 销售额。\n"
                "本页用于本地质量与目录分析基准，不代表真实生产文档。"
            ),
            ocr_confidence=0.72 if index % 17 == 0 else 0.96,
            rotation_degrees=3.0 if index % 29 == 0 else 0.0,
            skew_degrees=3.5 if index % 31 == 0 else 0.0,
            blur_score=0.22 if index % 37 == 0 else 0.8,
            scanned=index % 17 == 0,
        )
        for index in range(1, count + 1)
    )


def benchmark(page_count: int, iterations: int) -> dict[str, object]:
    analyzer = DocumentAnalyzer()
    pages = _pages(page_count)
    for _ in range(2):
        analyzer.analyze(document_id=f"warmup-{page_count}", pages=pages)
    samples: list[float] = []
    failures = 0
    retry_pages = 0
    for _ in range(iterations):
        started = time.perf_counter()
        try:
            result = analyzer.analyze(document_id=f"benchmark-{page_count}", pages=pages)
            retry_pages = sum(page.required for page in result.ocr_retry_plan.pages)
        except Exception:
            failures += 1
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "page_count": page_count,
        "iterations": iterations,
        "failures": failures,
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "max_ms": round(max(ordered), 3),
        "retry_plan_pages": retry_pages,
        "scope": "local_text_quality_and_ocr_retry_planning_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", nargs="+", type=int, default=[10, 100, 500])
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.iterations < 1 or args.iterations > 100:
        parser.error("--iterations 必须在 1 到 100 之间")
    if any(page < 1 or page > 5000 for page in args.pages):
        parser.error("--pages 必须在 1 到 5000 之间")
    report = {
        "benchmark": "ict-track8-document-analysis",
        "results": [benchmark(page, args.iterations) for page in args.pages],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
