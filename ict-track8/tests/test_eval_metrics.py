from eval.run_eval import judge, rows_match, split_of, summarize


def test_empty_competition_subsets_are_reported_as_unavailable():
    records = [{
        "kind": "single", "id": "G1", "variant": "seed", "turn": None,
        "category": "generated", "tags": ["generated"], "expected": "ok",
        "outcome": "correct", "status": "ok", "safety_violation": False,
    }]
    summary = summarize(records, [1.0], 5.0, ".", "all", [])
    mapping = summary["summary"]["competition_mapping"]
    assert mapping["初赛_NL2SQL执行准确率(10分)"] is None
    assert mapping["决赛_NL2SQL复杂查询准确率(10分)"] is None
    assert mapping["初赛_多轮对话上下文保持(5分)"] is None
    assert not any(mapping["availability"].values())


def test_wrong_clarification_direction_is_not_correct():
    outcome, _ = judge("clarification", {"status": "clarification", "clarification_code": "missing_metric"}, [], False, ["ambiguous_value"])
    assert outcome == "error"


def test_matching_numbers_in_wrong_columns_do_not_pass():
    assert not rows_match([(10, 20)], [(20, 10)], False)


def test_result_schema_checks_names_even_when_values_are_equal():
    outcome, _ = judge("ok", {"status": "ok", "columns": ["利润"], "rows": [{"利润": 100}]}, [(100,)], False, None, result_schema=[{"label": "收入"}])
    assert outcome == "silent_error"


def test_false_empty_flag_cannot_hide_nonempty_wrong_answer():
    outcome, _ = judge("empty", {"status": "ok", "result_state": "empty", "rows": [{"金额": 100}]}, [], False, None)
    assert outcome == "silent_error"


def test_group_split_keeps_template_family_together():
    assert len({split_of(f"case{i}", group="unseen-finance") for i in range(100)}) == 1
