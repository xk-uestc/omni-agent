"""对真实 8014 HTTP 检索做可重复延迟/失败率测量。

脚本不会打印 token，也不会把响应正文写入报告；不传 URL 时不会发起网络请求。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.retrieval_adapter import HttpManualRetriever


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("ICT8_MANUAL_RETRIEVER_URL", ""))
    parser.add_argument("--token", default=os.getenv("ICT8_MANUAL_RETRIEVER_TOKEN", ""))
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.url or not args.token:
        raise SystemExit("需要 --url/--token 或 ICT8_MANUAL_RETRIEVER_URL/ICT8_MANUAL_RETRIEVER_TOKEN")
    if not 1 <= args.iterations <= 100:
        raise SystemExit("--iterations 必须在 1 到 100 之间")
    retriever = HttpManualRetriever(args.url, args.token)
    health = retriever.health()
    samples: list[float] = []
    failures = 0
    hit_counts: list[int] = []
    questions = ("安全注意事项", "销售政策", "字段定义", "异常处理")
    for index in range(args.iterations):
        started = time.perf_counter()
        try:
            hits = retriever.search(questions[index % len(questions)], top_k=args.top_k)
            hit_counts.append(len(hits))
        except Exception:
            failures += 1
            hit_counts.append(0)
        samples.append((time.perf_counter() - started) * 1000)
    report = {
        "benchmark": "ict8-production-retrieval",
        "health": health,
        "iterations": args.iterations,
        "failures": failures,
        "failure_rate": round(failures / args.iterations, 4),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
        "average_hit_count": round(statistics.mean(hit_counts), 3),
        "scope": "real_http_retrieval_only; token_and_response_body_excluded",
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
