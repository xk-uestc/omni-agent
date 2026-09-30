from eval.run_eval import summarize


def test_summary_reports_planner_source_counts():
    common = {
        "kind": "single", "id": "X", "variant": "seed", "turn": None,
        "category": "simple", "tags": [], "expected": "ok", "outcome": "correct",
        "status": "ok", "safety_violation": False,
    }
    first = dict(common, planner_source="model_validated")
    second = dict(common, id="Y", planner_source="rules_fallback")
    report = summarize([first, second], [1.0, 1.0], 5.0, ".", "all", [])
    assert report["summary"]["planner_sources"] == {"model_validated": 1, "rules_fallback": 1}


def test_summary_reports_stable_planner_rejection_reasons():
    common = {
        "kind": "single", "id": "X", "variant": "seed", "turn": None,
        "category": "simple", "tags": [], "expected": "ok", "outcome": "correct",
        "status": "ok", "safety_violation": False,
    }
    record = dict(common, planner_source="rules_fallback", planner_audit={"fallback": True, "reason_code": "missing_time_range"})
    report = summarize([record], [1.0], 5.0, ".", "all", [])
    assert report["summary"]["planner_rejection_reasons"] == {"missing_time_range": 1}


def test_summary_reports_tag_coverage():
    record = {
        "kind": "single", "id": "X", "variant": "seed", "turn": None,
        "category": "blind", "tags": ["blind", "time"], "expected": "ok",
        "outcome": "correct", "status": "ok", "safety_violation": False,
        "planner_source": "rules",
    }
    report = summarize([record], [1.0], 5.0, ".", "all", [])
    assert report["summary"]["by_tag"]["time"] == {
        "n": 1, "correct": 1.0, "silent_error": 0.0, "clarification": 0.0
    }
