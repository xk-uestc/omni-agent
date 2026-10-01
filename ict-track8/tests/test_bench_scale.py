from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from eval.bench_scale import bench_structured
from backend.nl2sql.seed import initialize_database as real_initialize_database


class FakeEngine:
    def __init__(self, _path):
        pass

    def answer(self, question):
        if "同比" in question:
            raise RuntimeError("synthetic failure")
        return type("Result", (), {"status": "ok"})()


def initialize_database(path, force=True):
    return real_initialize_database(path, force=force)


def test_benchmark_reports_statuses_and_failures(tmp_path):
    rows = bench_structured(FakeEngine, initialize_database, [1], repeats=1, workdir=tmp_path)
    result = rows[0]
    assert result["status_counts"]["ok"] == 5
    assert result["status_counts"]["failed:RuntimeError"] == 1
    assert result["failed_total"] > 0
    assert all(item["failed"] > 0 for item in result["throughput_qps_by_threads"].values())


def test_benchmark_cli_fail_on_error_is_explicit(tmp_path):
    report = tmp_path / "bench.json"
    completed = subprocess.run(
        [sys.executable, "eval/bench_scale.py", "--repo", ".", "--rows", "1000", "--pages", "10",
         "--repeats", "1", "--out", str(report), "--fail-on-error"],
        capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert sum(row["failed_total"] for row in payload["structured"]) == 0
