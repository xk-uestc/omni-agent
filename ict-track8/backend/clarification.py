"""无状态主动澄清回填器：把用户选择转换为下一轮可审计问题。"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ClarificationSelection:
    code: str
    value: str
    label: str | None = None


class ClarificationResolver:
    _METRICS = {"sales_amount": "销售额", "quantity": "销量", "order_id": "订单数", "unit_price": "单价"}
    _DIMENSIONS = {"region": "按地区", "channel": "按渠道", "product_category": "按品类"}

    _SAFE_LABEL = re.compile(r"[\u3400-\u9fffA-Za-z0-9 _()（）·\-]{1,24}")

    def apply(self, original_question: str, selection: ClarificationSelection) -> str:
        question = (original_question or "").strip()
        if not question:
            raise ValueError("original_question 不能为空")
        if selection.code == "ambiguous_value":
            # 选项值由服务端生成："<原片段>=><具体取值>"；只做问题级替换，仍经完整规划与安全门
            span, sep, target = selection.value.partition("=>")
            if not sep or not self._SAFE_LABEL.fullmatch(span) or not self._SAFE_LABEL.fullmatch(target):
                raise ValueError("无效的取值选项")
            if span not in question.replace(" ", ""):
                raise ValueError("取值选项与原问题不匹配")
            compact = question.replace(" ", "")
            return compact.replace(span, target, 1)
        if selection.code in {"missing_metric", "ambiguous_metric", "missing_analysis_dimension", "missing_comparison_scope"} \
                and selection.value not in self._METRICS and selection.value not in self._DIMENSIONS and selection.value != "time_range":
            # Schema 驱动的选项（行业库等）：使用服务端下发的 label 回填
            if not selection.label or not self._SAFE_LABEL.fullmatch(selection.label):
                raise ValueError("该选项需要携带服务端下发的 label")
            return f"{question} {selection.label}"
        if selection.code in {"missing_metric", "ambiguous_metric"}:
            addition = self._METRICS.get(selection.value)
            if not addition:
                raise ValueError("无效的指标选项")
            return f"{question} {addition}"
        if selection.code == "missing_analysis_dimension":
            addition = self._DIMENSIONS.get(selection.value)
            if not addition:
                raise ValueError("无效的分组维度选项")
            return f"{question} {addition}"
        if selection.code == "missing_comparison_scope":
            addition = self._DIMENSIONS.get(selection.value)
            if selection.value == "time_range":
                raise ValueError("请直接补充具体年份或月份")
            if not addition:
                raise ValueError("无效的比较维度选项")
            return f"{question} {addition}"
        if selection.code == "missing_time_range":
            if selection.value == "monthly_trend":
                return f"{question} 按月"
            raise ValueError("请直接补充具体年份或月份，例如 2025 年")
        if selection.code == "ambiguous_join_path":
            if not re.fullmatch(r"[A-Za-z0-9_.>\-]+", selection.value):
                raise ValueError("无效的 JOIN 路径选项")
            return f"{question} [join_path:{selection.value}]"
        raise ValueError("不支持的澄清类型")
