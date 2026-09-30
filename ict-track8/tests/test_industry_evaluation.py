from __future__ import annotations

from pathlib import Path as _Path

DATA_DIR = _Path(__file__).resolve().parents[1] / "data"

from backend.evaluation import evaluate, load_cases
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.industry_seed import initialize_industry_database
from scripts.run_reference_samples import REFERENCE_CASES


def test_industry_evaluation_contract_is_green(tmp_path):
    engine = Nl2SqlEngine(
        initialize_industry_database(tmp_path / "industry-eval.sqlite"),
        aliases_path=DATA_DIR / "industry_aliases.json",
    )
    report = evaluate(engine, load_cases(DATA_DIR / "industry_nl2sql_eval_cases.json"))
    assert report["total"] == 14
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0


def test_reference_sample_selection_is_diverse_and_present():
    cases = {case.case_id for case in load_cases(DATA_DIR / "industry_nl2sql_eval_cases.json")}
    assert len(REFERENCE_CASES) == 5
    assert set(REFERENCE_CASES) <= cases
    assert "industry_missing_metric" in REFERENCE_CASES
    assert "industry_yoy" in REFERENCE_CASES
