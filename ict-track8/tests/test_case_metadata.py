from pathlib import Path

from eval.validate_cases import validate


def test_independent_blind_cases_have_valid_metadata():
    path = Path(__file__).resolve().parents[1] / "eval" / "cases" / "nl2sql_independent_blind.json"
    assert validate(path) == []


def test_official_case_pack_does_not_require_blind_tag():
    path = Path(__file__).resolve().parents[1] / "eval" / "cases" / "nl2sql_v2.json"
    assert validate(path) == []


def test_dialogue_case_may_omit_top_level_question(tmp_path):
    path = tmp_path / "dialogue.json"
    path.write_text(
        '{"version": 1, "cases": [{"id": "D1", "category": "dialogue", "db": "sales", '
        '"expected": "dialogue", "tags": ["context"], "variants": ["seed"], "safety": false, '
        '"turns": [{"question": "销售额", "expected": "ok", "gold_sql": "SELECT 1"}, '
        '{"question": "那华东呢", "expected": "ok", "gold_sql": "SELECT 1"}]}]}',
        encoding="utf-8",
    )
    assert validate(path) == []
