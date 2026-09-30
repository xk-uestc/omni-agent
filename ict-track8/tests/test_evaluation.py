from __future__ import annotations

from pathlib import Path as _Path

DATA_DIR = _Path(__file__).resolve().parents[1] / "data"

from backend.evaluation import evaluate, load_cases
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def test_evaluation_contract_runs_on_versioned_cases(tmp_path):
    cases = load_cases(DATA_DIR / "nl2sql_eval_cases.json")
    report = evaluate(Nl2SqlEngine(initialize_database(tmp_path / "eval.sqlite")), cases)
    assert report["total"] == 23
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0
