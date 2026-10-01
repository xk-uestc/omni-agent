"""赛题八非结构化文档质量、复杂度、目录和公式分析。

该模块接收 OCR/版面服务已经产出的文本与页面信号，不假装自己完成了图像 OCR。
所有评分都保留组成项和问题列表，便于答辩展示与离线回归。
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


_HEADING_RE = re.compile(
    r"^\s*(?:(#{1,6})\s+|第\s*[一二三四五六七八九十百千万0-9]+\s*[章节篇部]\s*|"
    r"([0-9]+(?:\.[0-9]+)*)(?:[、．]|\.(?!\d)|\s+)\s*|([一二三四五六七八九十]+)[、.．]\s*).{1,100}$"
)
_PAREN_HEADING_RE = re.compile(r"^[（(]([一二三四五六七八九十百0-9]+)[）)]\s*[^。；]{1,100}$")
_BRACKET_HEADING_RE = re.compile(r"^【[^【】。；]{2,60}】$")
_QUANTITY_LINE_RE = re.compile(r"^\d+(?:\.\d+)?\s*(?:亿元|万元|千元|元|小时|分钟|秒|千克|公斤|毫米|厘米|公里|%)(?=$|[\s，,。；;的])")
_FORMULA_RE = re.compile(
    r"^[ \t]*(?P<label>[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9 _-]{0,48})[ \t]*(?:=|＝|:|：)[ \t]*"
    r"(?P<expr>[^\r\n]{1,300})$", re.MULTILINE
)
_NON_FORMULA_LABEL = re.compile(r"电话|手机|传真|日期|时间|编号|号码|版本|邮编|型号|账号|序列号|ID|id|No", re.IGNORECASE)
_NON_FORMULA_VALUE = re.compile(r"\d{3,4}-\d{3,4}(?:-\d{3,4})?|\d{4}-\d{1,2}(?:-\d{1,2})?|\d{4}/\d{1,2}(?:/\d{1,2})?")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_OCR_CONFIDENCE_THRESHOLD = 0.8
_OCR_REPLACEMENT_THRESHOLD = 0.01
_ROTATION_THRESHOLD = 1.0
_SKEW_THRESHOLD = 2.0
_BLUR_THRESHOLD = 0.35


@dataclass(frozen=True)
class PageSignal:
    page_no: int
    text: str
    ocr_confidence: float = 1.0
    rotation_degrees: float = 0.0
    skew_degrees: float = 0.0
    blur_score: float | None = None
    scanned: bool = False


@dataclass(frozen=True)
class FormulaEvidence:
    label: str
    source_expression: str
    normalized_expression: str
    value: float | None
    status: str
    error: str | None = None
    parameters: tuple[str, ...] = ()
    line_no: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HeadingEvidence:
    text: str
    level: int
    line_no: int
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OCRRetryPage:
    """单页 OCR 重试决策；它描述计划，不执行外部 OCR。"""

    page_no: int
    required: bool
    reasons: tuple[str, ...]
    recommended_transforms: tuple[str, ...]
    max_attempts: int
    escalation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OCRRetryPlan:
    status: str
    policy: dict[str, Any]
    pages: tuple[OCRRetryPage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "policy": dict(self.policy),
            "pages": [page.to_dict() for page in self.pages],
        }


@dataclass(frozen=True)
class DocumentAnalysis:
    document_id: str
    quality_score: float
    complexity_score: float
    metrics: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    headings: tuple[HeadingEvidence, ...]
    formulas: tuple[FormulaEvidence, ...]
    ocr_retry_plan: OCRRetryPlan

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "quality_score": self.quality_score,
            "complexity_score": self.complexity_score,
            "metrics": self.metrics,
            "issues": [dict(item) for item in self.issues],
            "headings": [item.to_dict() for item in self.headings],
            "formulas": [item.to_dict() for item in self.formulas],
            "ocr_retry_plan": self.ocr_retry_plan.to_dict(),
        }


class SafeFormulaEvaluator:
    """只允许数字和基础四则运算的 AST 计算器。"""

    _BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
    _UNARYOPS = (ast.UAdd, ast.USub)

    def parameters(self, expression: str) -> tuple[str, ...]:
        if len(expression) > 300:
            raise ValueError("公式超出长度上限")
        tree = ast.parse(expression, mode="eval")
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, *self._BINOPS, *self._UNARYOPS)
        nodes = list(ast.walk(tree))
        if len(nodes) > 128 or any(not isinstance(node, allowed) for node in nodes):
            raise ValueError("表达式包含不支持的语法或超出复杂度上限")
        def depth(node):
            return 1 + max((depth(child) for child in ast.iter_child_nodes(node)), default=0)
        if depth(tree) > 20:
            raise ValueError("公式嵌套超出安全范围")
        if any(isinstance(node, ast.Constant) and (not isinstance(node.value, (int, float)) or isinstance(node.value, bool)) for node in nodes):
            raise ValueError("公式只能包含数值常量")
        return tuple(dict.fromkeys(node.id for node in nodes if isinstance(node, ast.Name)))

    def evaluate(self, expression: str, parameters: dict[str, float] | None = None) -> float:
        required = self.parameters(expression)
        supplied = parameters or {}
        if set(required) != set(supplied):
            raise ValueError("公式参数缺失或包含未引用的参数")
        for value in supplied.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e15:
                raise ValueError("公式参数必须为安全范围内的有限数字")
        tree = ast.parse(expression, mode="eval")
        return self._visit(tree.body, supplied)

    def _visit(self, node: ast.AST, parameters: dict[str, float]) -> float:
        if isinstance(node, ast.Name):
            return float(parameters[node.id])
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if not math.isfinite(float(node.value)) or abs(node.value) > 1e15:
                raise ValueError("数字不是有限值")
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, self._UNARYOPS):
            value = self._visit(node.operand, parameters)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, self._BINOPS):
            left, right = self._visit(node.left, parameters), self._visit(node.right, parameters)
            if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
                raise ValueError("除数不能为零")
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("指数超出安全范围")
            value = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.Pow: lambda: left**right,
                ast.Mod: lambda: left % right,
            }[type(node.op)]()
            if isinstance(value, complex) or not math.isfinite(value) or abs(value) > 1e15:
                raise ValueError("结果超出安全范围")
            return float(value)
        raise ValueError("表达式包含不支持的语法")


class DocumentAnalyzer:
    def __init__(self, *, formula_evaluator: SafeFormulaEvaluator | None = None):
        self.formula_evaluator = formula_evaluator or SafeFormulaEvaluator()

    def analyze(
        self,
        text: str = "",
        *,
        document_id: str = "document",
        pages: Iterable[PageSignal] | None = None,
    ) -> DocumentAnalysis:
        page_list = tuple(pages or ())
        combined_text = text or "\n".join(page.text for page in page_list)
        lines = combined_text.splitlines()
        headings = self._headings(lines)
        formulas = self._formulas(combined_text)
        issues: list[dict[str, Any]] = []
        metrics = self._metrics(combined_text, lines, page_list, headings, formulas)
        from .text_quality import text_quality
        metrics['text_quality'] = text_quality(combined_text)
        quality = self._quality(metrics, issues)
        for warning in metrics['text_quality']['warnings']:
            issues.append({'code': warning, 'action': 'review_text_repair_preview', 'original_preserved': True})
        complexity = self._complexity(metrics)
        ocr_retry_plan = self._ocr_retry_plan(page_list)
        return DocumentAnalysis(
            document_id=document_id,
            quality_score=round(quality, 4),
            complexity_score=round(complexity, 4),
            metrics=metrics,
            issues=tuple(issues),
            headings=tuple(headings),
            formulas=tuple(formulas),
            ocr_retry_plan=ocr_retry_plan,
        )

    @staticmethod
    def _ocr_retry_plan(pages: tuple[PageSignal, ...]) -> OCRRetryPlan:
        decisions: list[OCRRetryPage] = []
        for page in pages:
            reasons: list[str] = []
            transforms: list[str] = []
            replacement_rate = page.text.count("\ufffd") / max(1, len(page.text))
            if not page.text.strip():
                reasons.append("empty_text")
            if page.scanned:
                reasons.append("scanned_page")
            if page.ocr_confidence < _OCR_CONFIDENCE_THRESHOLD:
                reasons.append("low_ocr_confidence")
            if replacement_rate > _OCR_REPLACEMENT_THRESHOLD:
                reasons.append("ocr_replacement_chars")
            if abs(page.rotation_degrees) > _ROTATION_THRESHOLD:
                reasons.append("rotated_page")
                transforms.append("rotate_to_upright")
            if abs(page.skew_degrees) > _SKEW_THRESHOLD:
                reasons.append("skewed_page")
                transforms.append("deskew")
            if page.blur_score is not None and page.blur_score < _BLUR_THRESHOLD:
                reasons.append("blurred_page")
                transforms.append("sharpen")
            if page.scanned or page.ocr_confidence < _OCR_CONFIDENCE_THRESHOLD or replacement_rate > _OCR_REPLACEMENT_THRESHOLD:
                transforms.extend(("grayscale_normalize", "adaptive_threshold", "upscale"))
            required = bool(reasons)
            decisions.append(
                OCRRetryPage(
                    page_no=page.page_no,
                    required=required,
                    reasons=tuple(dict.fromkeys(reasons)),
                    recommended_transforms=tuple(dict.fromkeys(transforms)),
                    max_attempts=3 if required else 0,
                    escalation="external_ocr_after_retries" if required else "none",
                )
            )
        return OCRRetryPlan(
            status="required" if any(page.required for page in decisions) else "not_required",
            policy={
                "ocr_confidence_threshold": _OCR_CONFIDENCE_THRESHOLD,
                "replacement_char_rate_threshold": _OCR_REPLACEMENT_THRESHOLD,
                "rotation_threshold_degrees": _ROTATION_THRESHOLD,
                "skew_threshold_degrees": _SKEW_THRESHOLD,
                "blur_score_threshold": _BLUR_THRESHOLD,
                "max_attempts": 3,
                "does_not_execute_ocr": True,
            },
            pages=tuple(decisions),
        )

    @staticmethod
    def _headings(lines: list[str]) -> list[HeadingEvidence]:
        result: list[HeadingEvidence] = []
        for index, line in enumerate(lines, start=1):
            value = line.strip()
            if not value or len(value) > 120:
                continue
            if _QUANTITY_LINE_RE.match(value):
                continue
            if _PAREN_HEADING_RE.fullmatch(value):
                result.append(HeadingEvidence(value, 2, index, "parenthesized"))
                continue
            if _BRACKET_HEADING_RE.fullmatch(value):
                result.append(HeadingEvidence(value, 2, index, "bracket_heuristic"))
                continue
            match = _HEADING_RE.match(value)
            if not match:
                continue
            if match.group(1):
                level, rule = len(match.group(1)), "markdown"
            elif value.startswith("第"):
                level, rule = 1, "chapter"
            elif match.group(2):
                level, rule = min(6, match.group(2).count(".") + 1), "numeric"
            elif match.group(3):
                level, rule = 1, "cjk_numeric"
            else:
                level, rule = 1, "cjk_numeric"
            result.append(HeadingEvidence(value, level, index, rule))
        return result

    def _formulas(self, text: str) -> list[FormulaEvidence]:
        result: list[FormulaEvidence] = []
        for match in _FORMULA_RE.finditer(text):
            source = match.group("expr").strip()
            label = match.group("label").strip()
            if _NON_FORMULA_LABEL.search(label) or _NON_FORMULA_VALUE.fullmatch(source.replace(" ", "")):
                # 电话、日期、编号等"形似算式"的文本不是公式，不参与计算
                continue
            normalized = self._normalize_formula(source)
            line_no = text.count("\n", 0, match.start()) + 1
            try:
                parameters = self.formula_evaluator.parameters(normalized)
                if parameters:
                    result.append(FormulaEvidence(label, source, normalized, None, "requires_parameters", parameters=parameters, line_no=line_no))
                    continue
                value = self.formula_evaluator.evaluate(normalized)
                result.append(FormulaEvidence(label, source, normalized, round(value, 10), "evaluated", line_no=line_no))
            except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
                result.append(FormulaEvidence(label, source, normalized, None, "rejected", str(exc), line_no=line_no))
        return result

    @staticmethod
    def _normalize_formula(expression: str) -> str:
        translation = str.maketrans("０１２３４５６７８９．＋−×÷％（）", "0123456789.+-*/%()")
        value = expression.translate(translation).replace("×", "*").replace(" ", "").replace("\t", "")
        # x is a valid parameter name; only the actual multiplication glyph is translated.
        value = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)%(?![\w.])", r"(\1/100)", value)
        return value

    @staticmethod
    def _metrics(
        text: str,
        lines: list[str],
        pages: tuple[PageSignal, ...],
        headings: list[HeadingEvidence],
        formulas: list[FormulaEvidence],
    ) -> dict[str, Any]:
        chars = len(text)
        replacement_count = text.count("\ufffd")
        non_space = [char for char in text if not char.isspace()]
        cjk_count = len(_CJK_RE.findall(text))
        words = len(_WORD_RE.findall(text))
        table_lines = sum(1 for line in lines if line.count("|") >= 2 or "\t" in line)
        avg_ocr = sum(page.ocr_confidence for page in pages) / len(pages) if pages else 1.0
        rotations = [abs(page.rotation_degrees) for page in pages]
        skews = [abs(page.skew_degrees) for page in pages]
        blur_scores = [page.blur_score for page in pages if page.blur_score is not None]
        return {
            "character_count": chars,
            "content_character_count": len(non_space),
            "line_count": len(lines),
            "page_count": len(pages) or 1,
            "heading_count": len(headings),
            "formula_count": len(formulas),
            "evaluated_formula_count": sum(item.status == "evaluated" for item in formulas),
            "table_like_line_count": table_lines,
            "cjk_ratio": round(cjk_count / max(1, len(non_space)), 4),
            "word_count": words,
            "replacement_char_rate": round(replacement_count / max(1, chars), 4),
            "average_ocr_confidence": round(avg_ocr, 4),
            "min_page_ocr_confidence": round(min(page.ocr_confidence for page in pages), 4) if pages else None,
            "max_rotation_degrees": round(max(rotations, default=0.0), 3),
            "max_skew_degrees": round(max(skews, default=0.0), 3),
            "min_blur_score": round(min(blur_scores), 4) if blur_scores else None,
            "scanned_page_count": sum(page.scanned for page in pages),
        }

    @staticmethod
    def _quality(metrics: dict[str, Any], issues: list[dict[str, Any]]) -> float:
        score = 1.0
        if metrics["content_character_count"] == 0:
            issues.append({"code": "no_text", "severity": "error", "message": "未提取到可用文本"})
            return 0.0
        replacement_rate = metrics["replacement_char_rate"]
        if replacement_rate > _OCR_REPLACEMENT_THRESHOLD:
            penalty = min(0.35, replacement_rate * 2)
            score -= penalty
            issues.append({"code": "ocr_replacement_chars", "severity": "warning", "message": "OCR 含替换字符", "rate": replacement_rate})
        confidence = metrics["average_ocr_confidence"]
        if confidence < _OCR_CONFIDENCE_THRESHOLD:
            score -= min(0.3, (0.8 - confidence) * 0.9)
            issues.append({"code": "low_ocr_confidence", "severity": "warning", "message": "OCR 平均置信度偏低", "value": confidence})
        if metrics["max_rotation_degrees"] > _ROTATION_THRESHOLD:
            score -= min(0.18, metrics["max_rotation_degrees"] / 45)
            issues.append({"code": "rotated_page", "severity": "warning", "message": "存在旋转页面", "degrees": metrics["max_rotation_degrees"]})
        if metrics["max_skew_degrees"] > _SKEW_THRESHOLD:
            score -= min(0.18, metrics["max_skew_degrees"] / 30)
            issues.append({"code": "skewed_page", "severity": "warning", "message": "存在倾斜页面", "degrees": metrics["max_skew_degrees"]})
        if metrics["min_blur_score"] is not None and metrics["min_blur_score"] < _BLUR_THRESHOLD:
            score -= min(0.2, 0.35 - metrics["min_blur_score"])
            issues.append({"code": "blurred_page", "severity": "warning", "message": "存在清晰度不足页面", "score": metrics["min_blur_score"]})
        scanned_ratio = metrics["scanned_page_count"] / max(1, metrics["page_count"])
        if scanned_ratio > 0:
            score -= min(0.25, 0.25 * scanned_ratio)
            issues.append({"code": "scanned_pages", "severity": "warning", "message": "存在无文字层或文字层过少的页面，需要 OCR", "ratio": round(scanned_ratio, 4)})
        if metrics["min_page_ocr_confidence"] is not None and metrics["min_page_ocr_confidence"] < 0.6:
            score -= 0.1
            issues.append({"code": "worst_page_low_confidence", "severity": "warning", "message": "存在置信度极低的单页（平均值可能掩盖）", "value": metrics["min_page_ocr_confidence"]})
        if metrics["heading_count"] == 0 and metrics["line_count"] > 20:
            score -= 0.08
            issues.append({"code": "nonstandard_outline", "severity": "info", "message": "未识别到标准目录层级，将按段落顺序组织"})
        return max(0.0, min(1.0, score))

    @staticmethod
    def _complexity(metrics: dict[str, Any]) -> float:
        raw = (
            min(1.0, metrics["page_count"] / 100)
            + min(1.0, metrics["character_count"] / 100_000)
            + min(1.0, metrics["heading_count"] / 50)
            + min(1.0, metrics["formula_count"] / 20)
            + min(1.0, metrics["table_like_line_count"] / 50)
        ) / 5
        return max(0.0, min(1.0, raw))
