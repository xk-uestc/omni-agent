from __future__ import annotations

from backend.document_analysis import DocumentAnalyzer, PageSignal, SafeFormulaEvaluator


def test_formula_is_safely_evaluated():
    result = DocumentAnalyzer().analyze("销售额 = 120 * 3\n税率 = １２．５％")
    assert [item.status for item in result.formulas] == ["evaluated", "evaluated"]
    assert result.formulas[0].value == 360.0
    assert result.formulas[1].value == 0.125


def test_unsafe_formula_is_rejected():
    evaluator = SafeFormulaEvaluator()
    try:
        evaluator.evaluate("__import__('os').system('whoami')")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe AST was evaluated")


def test_formula_division_by_zero_is_rejected():
    result = DocumentAnalyzer().analyze("比例 = 10 / 0")
    assert result.formulas[0].status == "rejected"
    assert "除数" in result.formulas[0].error


def test_nonstandard_headings_are_recovered():
    result = DocumentAnalyzer().analyze("第一章 总则\n1.1 范围\n二、定义\n正文")
    assert [item.rule for item in result.headings] == ["chapter", "numeric", "cjk_numeric"]
    assert result.metrics["heading_count"] == 3


def test_quality_reports_rotation_skew_blur_and_ocr():
    result = DocumentAnalyzer().analyze(
        pages=[
            PageSignal(1, "正文\ufffd", ocr_confidence=0.55, rotation_degrees=4, skew_degrees=6, blur_score=0.2, scanned=True),
            PageSignal(2, "正文", ocr_confidence=0.9, scanned=True),
        ]
    )
    codes = {item["code"] for item in result.issues}
    assert result.quality_score < 0.8
    assert {"ocr_replacement_chars", "low_ocr_confidence", "rotated_page", "skewed_page", "blurred_page"} <= codes
    assert result.metrics["scanned_page_count"] == 2


def test_ocr_retry_plan_is_page_scoped_and_ordered():
    result = DocumentAnalyzer().analyze(
        pages=[
            PageSignal(1, "正文\ufffd", ocr_confidence=0.55, rotation_degrees=4, skew_degrees=6, blur_score=0.2, scanned=True),
            PageSignal(2, "清晰正文", ocr_confidence=0.97, scanned=False, blur_score=0.8),
        ]
    )
    plan = result.ocr_retry_plan.to_dict()
    assert plan["status"] == "required"
    assert plan["policy"]["does_not_execute_ocr"] is True
    assert plan["pages"][0]["required"] is True
    assert plan["pages"][0]["max_attempts"] == 3
    assert plan["pages"][0]["recommended_transforms"] == (
        "rotate_to_upright",
        "deskew",
        "sharpen",
        "grayscale_normalize",
        "adaptive_threshold",
        "upscale",
    )
    assert plan["pages"][1]["required"] is False
    assert plan["pages"][1]["max_attempts"] == 0


def test_empty_document_is_explicitly_low_quality():
    result = DocumentAnalyzer().analyze("")
    assert result.quality_score == 0.0
    assert result.issues[0]["code"] == "no_text"
