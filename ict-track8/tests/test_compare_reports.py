from __future__ import annotations

from eval.compare_reports import compare


def _report(records):
    return {"summary": {"silent_error_rate": 0, "safety_violations": 0, "database_files_modified": []},
            "records": records}


def _record(case_id="A", variant="seed", turn=None):
    return {"kind": "single", "id": case_id, "variant": variant, "turn": turn,
            "outcome": "correct", "detail": "ok"}


def test_missing_candidate_units_fail_closed():
    text, failed = compare(_report([_record("A"), _record("B")]), _report([_record("A")]))
    assert failed
    assert "缺少 1 个评测单元" in text


def test_unexpected_candidate_units_fail_closed():
    text, failed = compare(_report([_record("A")]), _report([_record("A"), _record("B")]))
    assert failed
    assert "新增 1 个评测单元" in text


def test_duplicate_coordinates_fail_closed():
    text, failed = compare(_report([_record("A")]), _report([_record("A"), _record("A")]))
    assert failed
    assert "重复单元" in text
