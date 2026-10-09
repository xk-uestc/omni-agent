"""Run a sequential, redacted latency and correctness comparison for model gateways."""
from __future__ import annotations

import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ict-track8"))
sys.path.insert(0, str(ROOT / "tools"))

from model_runtime import enable_local_model
from backend.responses_client import GenerationError, StructuredResponses, object_schema


TASKS = [
    {"id": "deadline", "facts": "签收通知后5日内反馈。", "question": "反馈期限是多少？只给期限。",
     "pattern": r"^.*(?:5|五)(?:日|天).*$"},
    {"id": "discount", "facts": "商品原价342000元，优惠率12.5%。", "question": "优惠金额是多少元？只给数字。",
     "pattern": r"^.*42750(?:\.0+)?(?:元)?$"},
    {"id": "difference", "facts": "华东2025年收入4188944元，华南2025年收入3688944元。",
     "question": "华东比华南高多少元？只给数字。", "pattern": r"^.*500000(?:\.0+)?(?:元)?$"},
    {"id": "selection", "facts": "A产品评分88分，B产品评分92分。",
     "question": "哪款产品评分更高？只输出产品代号。", "pattern": r"^B.*$"},
    {"id": "abstention", "facts": "制度规定每年复核，资料未给出合同最长期限。",
     "question": "合同最长期限是多少年？资料未说明时请回答‘资料未说明’。",
     "pattern": r".*(?:资料未说明|未说明|无法确定).*"},
]
SCHEMA = object_schema({"answer": {"type": "string"}})
MODELS = ("gpt-6-luna", "gpt-5.6-luna")
REPETITIONS = 10


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def clean_answer(value: str) -> str:
    return re.sub(r"[\s,，。:：]", "", value).upper()


def main() -> int:
    enable_local_model()
    configured = json.loads(os.environ["ICT8_OPENAI_FALLBACKS"])
    providers = []
    for item in configured:
        providers.append({
            "provider": item["provider"],
            "base_url": item["base_url"],
            "api_key": item["api_key"],
            "host": urlsplit(item["base_url"]).hostname,
        })
    names = {item["provider"] for item in providers}
    required = {"conpera", "yescode", "spacetime"}
    if names != required:
        raise SystemExit("备用配置未完整包含三家指定服务，未发送测试请求。")

    combos = []
    for provider in providers:
        for model in MODELS:
            combos.append({
                "provider": provider["provider"], "host": provider["host"], "model": model,
                "client": StructuredResponses(
                    provider["base_url"], provider["api_key"], model=model,
                    reasoning="medium", timeout=45, transport_attempts=1,
                ),
            })

    attempts = []
    rng = random.Random(20261008)
    for repetition in range(REPETITIONS):
        round_combos = list(combos)
        rng.shuffle(round_combos)
        task = TASKS[repetition % len(TASKS)]
        for combo in round_combos:
            client = combo["client"]
            started = time.perf_counter()
            result = None
            error_type = None
            try:
                result = client.generate(
                    "严格依据给定资料回答。只返回符合 JSON Schema 的 answer 字段；答案简洁，不补充推测。",
                    {"facts": task["facts"], "question": task["question"]},
                    SCHEMA, name="stability_probe", max_tokens=120,
                )
            except (GenerationError, Exception) as exc:
                error_type = type(exc).__name__
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            audit = client.audit
            answer = result.get("answer") if isinstance(result, dict) else None
            if not isinstance(answer, str):
                answer = None
            verified = (audit.get("status") == "completed" and audit.get("model_verified") is True
                        and audit.get("response_model") == combo["model"])
            answer_field_string = answer is not None
            correct = bool(answer_field_string and re.fullmatch(task["pattern"], clean_answer(answer), re.I))
            attempts.append({
                "round": repetition + 1, "task": task["id"], "provider": combo["provider"],
                "host": combo["host"], "model": combo["model"],
                "elapsed_ms": audit.get("latency_ms", elapsed_ms), "http_status": audit.get("http_status"),
                "verified_model": audit.get("response_model"), "model_verified": verified,
                "answer_field_string": answer_field_string, "answer_correct": correct,
                "answer": answer, "transport_attempts": audit.get("transport_attempts"),
                "transport_failures": audit.get("transport_failures", []),
                "error_type": error_type,
            })
            print(json.dumps({key: attempts[-1][key] for key in (
                "round", "task", "provider", "model", "elapsed_ms", "http_status",
                "model_verified", "answer_field_string", "answer_correct", "answer", "error_type")}, ensure_ascii=False),
                flush=True)

    summaries = []
    for combo in combos:
        rows = [row for row in attempts if row["provider"] == combo["provider"] and row["model"] == combo["model"]]
        latencies = [row["elapsed_ms"] for row in rows if isinstance(row["elapsed_ms"], (int, float))]
        summaries.append({
            "provider": combo["provider"], "host": combo["host"], "model": combo["model"],
            "attempts": len(rows), "model_verified": sum(row["model_verified"] for row in rows),
            "answer_field_string": sum(row["answer_field_string"] for row in rows),
            "answer_correct": sum(row["answer_correct"] for row in rows),
            "request_failures": sum(not row["model_verified"] for row in rows),
            "http_5xx": sum(isinstance(row["http_status"], int) and row["http_status"] >= 500 for row in rows),
            "timeouts": sum(row["http_status"] is None and row["error_type"] is not None for row in rows),
            "answer_contract_failures": sum(row["model_verified"] and not row["answer_field_string"] for row in rows),
            "median_ms": round(statistics.median(latencies), 1) if latencies else None,
            "p95_ms_interpolated": round(percentile(latencies, 0.95), 1) if latencies else None,
            "mean_ms": round(statistics.mean(latencies), 1) if latencies else None,
            "stdev_ms": round(statistics.stdev(latencies), 1) if len(latencies) > 1 else 0,
            "min_ms": round(min(latencies), 1) if latencies else None,
            "max_ms": round(max(latencies), 1) if latencies else None,
        })

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "scope": "3 providers x 2 model IDs x 10 sequential calls; 5 fixed Chinese tasks, each repeated twice",
        "settings": {"reasoning": "medium", "service_tier": "unset for fair comparison",
                     "transport_retries": 0, "timeout_seconds": 45, "parallel_requests": 1},
        "catalog_preflight": "all three provider catalogs listed both requested model IDs",
        "summaries": summaries,
        "attempts": attempts,
    }
    destination = ROOT / "runtime" / f"provider-model-stability-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(destination), "summaries": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
