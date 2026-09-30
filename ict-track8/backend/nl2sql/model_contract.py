"""外部模型规划结果的严格 JSON 契约和安全校验。

模型只负责提出结构化计划，不能提交 SQL。这里把计划中的每个标识符与实际
SQLite Schema 对齐，并让现有规划器依据真实外键图生成 JOIN 条件，最后仍由
同一只读 SQL 安全门执行。
"""

from __future__ import annotations

import json
import math
import time
from datetime import date
from collections.abc import Mapping
from typing import Any, Protocol

import requests

from .models import DerivedMetricSpec, FilterSpec, HavingSpec, LinkCandidate, MetricSpec, QueryPlan, TableInfo
from .metric_compiler import MetricCompiler, MetricPlanError
from .planner import SingleTablePlanner


class ModelPlanError(ValueError):
    """模型计划不符合可执行契约。"""


class ModelPlanProvider(Protocol):
    def __call__(self, question: str, tables: tuple[TableInfo, ...]) -> Mapping[str, Any]:
        ...


class HttpModelPlanProvider:
    """调用外部模型规划服务，只传 Schema 快照和用户问题。

    服务只需要返回纯 JSON 计划，格式可以是计划对象本身，也可以是
    ``{"plan": {...}}``。模型服务永远不能提交 SQL；后续校验和生成仍在本地完成。
    """

    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        timeout: float = 8.0,
        max_retries: int = 1,
        session: Any | None = None,
    ):
        value = str(url or "").strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("模型规划 URL 必须使用 http 或 https")
        if not 0.1 <= float(timeout) <= 60.0:
            raise ValueError("模型规划 timeout 必须在 0.1 到 60 秒之间")
        if not 0 <= int(max_retries) <= 2:
            raise ValueError("模型规划 max_retries 必须在 0 到 2 之间")
        self.url = value
        self.token = str(token or "")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.session = session or requests.Session()

    def __call__(self, question: str, tables: tuple[TableInfo, ...]) -> Mapping[str, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = {"question": str(question), "schema": {"tables": [table.to_dict() for table in tables]}}
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if attempt >= self.max_retries or not retryable:
                    raise ModelPlanError("模型规划服务暂不可用，已回退规则规划") from exc
                time.sleep(0.05 * (2**attempt))
        if response is None:
            raise ModelPlanError("模型规划服务没有返回响应，已回退规则规划")
        try:
            parsed = response.json()
        except (TypeError, ValueError) as exc:
            raise ModelPlanError("模型规划服务返回的 JSON 无法解析，已回退规则规划") from exc
        if isinstance(parsed, Mapping) and isinstance(parsed.get("plan"), Mapping):
            parsed = parsed["plan"]
        if not isinstance(parsed, Mapping):
            raise ModelPlanError("模型规划服务返回的计划不是对象，已回退规则规划")
        return parsed


_FUNCTIONS = {"SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"}
_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "LIKE"}
_RANGE_OPERATORS = {"BETWEEN", "RANGE"}  # BETWEEN=两端闭区间（SQL 标准）；RANGE=[start, end) 半开区间
_SET_OPERATORS = {"IN", "NOT IN"}
_TRANSFORMS = {"raw", "month", "year"}
_MODES = {"aggregate", "rank", "share"}
_ROLES = {"metric", "dimension"}


def parse_model_json(raw: str, *, max_chars: int = 50_000) -> dict[str, Any]:
    """解析纯 JSON；不接受 Markdown 围栏、截断或顶层数组。"""

    if not isinstance(raw, str) or not raw.strip():
        raise ModelPlanError("模型计划不能为空")
    if len(raw) > max_chars:
        raise ModelPlanError("模型计划超过大小限制")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelPlanError("模型计划必须是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ModelPlanError("模型计划顶层必须是对象")
    return payload


class ModelPlanValidator:
    """把模型 JSON 转换成受 Schema 约束的 :class:`QueryPlan`。"""

    def __init__(self, *, max_join_hops: int = 4):
        self.join_resolver = SingleTablePlanner(max_join_hops=max_join_hops)

    def validate(
        self,
        payload: Mapping[str, Any],
        tables: tuple[TableInfo, ...],
        *,
        question: str,
    ) -> QueryPlan:
        if not isinstance(payload, Mapping):
            raise ModelPlanError("模型计划必须是 JSON 对象")
        if payload.get("version", 1) == 2:
            return self._validate_v2(payload, tables, question=question)
        if payload.get("version", 1) != 1:
            raise ModelPlanError("不支持的模型计划版本")
        by_name = {table.name: table for table in tables}
        table_name = self._identifier(payload.get("table"), "table")
        table = by_name.get(table_name)
        if table is None:
            raise ModelPlanError(f"模型选择了不存在的表: {table_name}")
        metric = payload.get("metric")
        if not isinstance(metric, Mapping):
            raise ModelPlanError("metric 必须是对象")
        metric_table_name = self._identifier(metric.get("table", table_name), "metric.table")
        metric_table = by_name.get(metric_table_name)
        if metric_table is None:
            raise ModelPlanError(f"模型选择了不存在的指标表: {metric_table_name}")
        metric_column = self._identifier(metric.get("column"), "metric.column")
        metric_column_info = self._column(by_name, metric_table_name, metric_column, "metric.column")
        function = str(metric.get("function", "")).upper()
        if function not in _FUNCTIONS:
            raise ModelPlanError(f"不允许的聚合函数: {function}")
        if function in {"SUM", "AVG", "MIN", "MAX"} and not self._numeric(metric_column_info.data_type):
            raise ModelPlanError("数值聚合只能作用于数值列")
        metric_label = self._label(metric.get("label"), metric_column)

        dimensions, dimension_tables, transforms, labels = self._dimensions(payload.get("dimensions", []), by_name, metric_table_name, metric_column)
        mode = str(payload.get("analysis_mode", "aggregate")).lower()
        if mode not in _MODES:
            raise ModelPlanError(f"不支持的 analysis_mode: {mode}")
        if mode in {"rank", "share"} and not dimensions:
            raise ModelPlanError(f"{mode} 模式必须提供 dimensions")
        filters = self._filters(payload.get("filters", []), by_name)
        having = self._having(payload.get("having"), by_name, metric_table_name, metric_column)
        comparison_mode, comparison_period = self._comparison(payload.get("comparison"), by_name)
        if comparison_mode != "none" and mode in {"rank", "share"}:
            raise ModelPlanError("同比/环比不能与 rank/share 同时使用")
        if comparison_mode != "none" and having is not None:
            raise ModelPlanError("同比/环比不能与 having 同时使用")

        required_tables = {
            table_name,
            metric_table_name,
            *(dimension_tables.values()),
            *(item.table or table_name for item in filters),
        }
        if comparison_mode != "none":
            required_tables.add(comparison_period["date_table"])
        path_hint = self._path_hint(payload.get("join_path"))
        joins = self.join_resolver.resolve_joins(table_name, required_tables, tables, path_hint=path_hint)
        if joins is None:
            raise ModelPlanError("模型计划中的表没有可验证的外键 JOIN 路径")
        join_tables, join_conditions, join_path, alternatives = joins
        if alternatives:
            raise ModelPlanError("模型计划未消除多条同长度 JOIN 路径歧义")
        if path_hint and ">".join([table_name, *[item["to_table"] for item in join_path]]) != path_hint:
            raise ModelPlanError("模型提供的 join_path 与 Schema 验证结果不一致")

        requested_limit = self._limit(payload.get("limit", 100))
        limit = min(requested_limit, 100)
        confidence = self._confidence(payload.get("confidence", 0.0))
        rewritten = str(payload.get("rewritten_question") or question).strip()
        if not rewritten:
            raise ModelPlanError("rewritten_question 不能为空")
        links = [
            LinkCandidate(metric_column, metric_table_name, metric_column, "metric", confidence, metric_label),
            *[
                LinkCandidate(column, dimension_tables[column], column, "dimension", confidence, labels.get(column, column))
                for column in dimensions
            ],
        ]
        order_desc = payload.get("order_desc", True)
        if not isinstance(order_desc, bool):
            raise ModelPlanError("order_desc 必须是布尔值")
        top_n = payload.get("top_n")
        if top_n is not None and (isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 100 or not dimensions):
            raise ModelPlanError("top_n 必须为 1 到 100 的整数且需要分组维度")
        # 关联基数以指标事实表为根重新计算，不能接受模型自行宣称的 fan_out。
        metric_joins = self.join_resolver.resolve_joins(metric_table_name, required_tables, tables)
        if metric_joins is None or metric_joins[3]:
            raise ModelPlanError("指标事实表的 JOIN 粒度无法验证")
        fan_out = any(edge["cardinality"] == "one_to_many" for edge in metric_joins[2])
        plan = QueryPlan(
            table=table_name,
            metric_table=metric_table_name,
            metric_column=metric_column,
            metric_function=function,
            metric_label=metric_label,
            analysis_mode=mode,
            dimensions=dimensions,
            dimension_tables=dimension_tables,
            dimension_transforms=transforms,
            dimension_labels=labels,
            filters=filters,
            having=having,
            links=links,
            join_tables=join_tables,
            join_conditions=join_conditions,
            join_path=join_path,
            order_desc=order_desc,
            limit=limit,
            confidence=confidence,
            rewritten_question=rewritten,
            comparison_mode=comparison_mode,
            comparison_period=comparison_period,
            assumptions=["模型计划已通过 Schema、外键图和只读执行契约校验"],
            planner_source="model_validated",
            top_n=top_n,
            fan_out=fan_out,
        )
        if requested_limit > 100:
            plan.assumptions.append(f"结果上限 {requested_limit} 超过安全上限，已限制为 100")
        if fan_out:
            try:
                MetricCompiler(self.join_resolver).compile(plan, tables)
            except MetricPlanError as exc:
                raise ModelPlanError(str(exc)) from exc
        return plan

    def _validate_v2(self, payload, tables, *, question):
        raw_metrics = payload.get("metrics")
        if not isinstance(raw_metrics, list) or not 1 <= len(raw_metrics) <= 8:
            raise ModelPlanError("metrics 必须包含 1 到 8 个指标")
        if payload.get("comparison") is not None and len(raw_metrics) == 1 and not payload.get("derived_metrics"):
            return self.validate({**payload, "version": 1, "table": raw_metrics[0].get("table"), "metric": raw_metrics[0]}, tables, question=question)
        metrics, plans = [], []
        for i, raw in enumerate(raw_metrics):
            if not isinstance(raw, Mapping):
                raise ModelPlanError("指标必须是对象")
            item = dict(payload)
            item.update(version=1, metric=raw, table=raw.get("table"), comparison=None)
            # 对每个事实源校验同一批维度与条件；所有 JOIN 必须来自实际 Schema。
            checked = self.validate(item, tables, question=question)
            metric_id = self._identifier(raw.get("id", f"m{i}"), "metric.id")
            unit = raw.get("unit", "unknown")
            currency = raw.get("currency")
            if not isinstance(unit, str) or not 1 <= len(unit) <= 40 or currency is not None and (not isinstance(currency, str) or len(currency) > 10):
                raise ModelPlanError("指标单位/币种无效")
            missing = raw.get("missing", "null")
            if missing not in {"null", "zero"}:
                raise ModelPlanError("指标缺失策略无效")
            specific_filters = self._filters(raw.get("filters", []), {t.name: t for t in tables})
            metrics.append(MetricSpec(metric_id, checked.metric_table, checked.metric_column,
                                      checked.metric_function, checked.metric_label, unit, currency, missing, specific_filters))
            plans.append(checked)
        plan = plans[0]
        plan.metrics = metrics
        plan.links = [link for p in plans for link in p.links]
        derived = payload.get("derived_metrics", [])
        if not isinstance(derived, list) or len(derived) > 8:
            raise ModelPlanError("derived_metrics 最多 8 项")
        for item in derived:
            if not isinstance(item, Mapping) or not isinstance(item.get("expression"), dict):
                raise ModelPlanError("派生指标需要表达式树")
            plan.derived_metrics.append(DerivedMetricSpec(self._identifier(item.get("id"), "derived.id"),
                                                        self._label(item.get("label"), item["id"]), item["expression"]))
        order = payload.get("order_metric")
        plan.order_metric = self._identifier(order, "order_metric") if order is not None else None
        outputs = payload.get("output_metrics", [])
        if not isinstance(outputs, list) or len(outputs) > 16:
            raise ModelPlanError("output_metrics 必须是指标 ID 数组")
        plan.output_metrics = [self._identifier(m, "output_metric") for m in outputs]
        if payload.get("comparison") is not None:
            raise ModelPlanError("多指标比较需要逐指标时间口径，当前不接受隐含口径")
        try:
            MetricCompiler(self.join_resolver).compile(plan, tables)
        except MetricPlanError as exc:
            raise ModelPlanError(str(exc)) from exc
        return plan

    @staticmethod
    def _comparison(raw: Any, by_name: dict[str, TableInfo]) -> tuple[str, dict[str, str]]:
        if raw is None:
            return "none", {}
        if not isinstance(raw, Mapping):
            raise ModelPlanError("comparison 必须是对象")
        mode = str(raw.get("mode", "")).strip()
        if mode not in {"同比", "环比"}:
            raise ModelPlanError("comparison.mode 只能是 同比 或 环比")
        table = ModelPlanValidator._identifier(raw.get("date_table"), "comparison.date_table")
        column = ModelPlanValidator._identifier(raw.get("date_column"), "comparison.date_column")
        date_info = ModelPlanValidator._column(by_name, table, column, "comparison.date_column")
        if not ModelPlanValidator._date(date_info.name):
            raise ModelPlanError("comparison.date_column 必须是日期列")
        fields = ("current_start", "current_end", "previous_start", "previous_end")
        period = {"date_table": table, "date_column": column}
        parsed: dict[str, date] = {}
        for field in fields:
            value = raw.get(field)
            if not isinstance(value, str):
                raise ModelPlanError(f"{field} 必须是 ISO 日期")
            try:
                parsed[field] = date.fromisoformat(value)
            except ValueError as exc:
                raise ModelPlanError(f"{field} 必须是 ISO 日期") from exc
            period[field] = value
        if not parsed["current_start"] < parsed["current_end"] or not parsed["previous_start"] < parsed["previous_end"]:
            raise ModelPlanError("comparison 时间窗口必须为正区间")
        if parsed["previous_end"] > parsed["current_start"]:
            raise ModelPlanError("comparison 时间窗口不能重叠")
        return mode, period

    def _dimensions(self, raw: Any, by_name: dict[str, TableInfo], metric_table: str, metric_column: str):
        if not isinstance(raw, list) or len(raw) > 8:
            raise ModelPlanError("dimensions 必须是不超过 8 项的数组")
        dimensions: list[str] = []
        tables: dict[str, str] = {}
        transforms: dict[str, str] = {}
        labels: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, Mapping):
                raise ModelPlanError("dimension 项必须是对象")
            table = self._identifier(item.get("table", metric_table), "dimension.table")
            column = self._identifier(item.get("column"), "dimension.column")
            info = self._column(by_name, table, column, "dimension.column")
            if table == metric_table and column == metric_column:
                raise ModelPlanError("指标列不能同时作为分组维度")
            if column in dimensions:
                raise ModelPlanError(f"dimension 重复: {column}")
            transform = str(item.get("transform", "raw")).lower()
            if transform not in _TRANSFORMS:
                raise ModelPlanError(f"不支持的 dimension transform: {transform}")
            if transform != "raw" and not self._date(info.name):
                raise ModelPlanError("month/year 变换只能作用于日期列")
            dimensions.append(column)
            tables[column] = table
            if transform != "raw":
                transforms[column] = transform
            labels[column] = self._label(item.get("label"), column)
        return dimensions, tables, transforms, labels

    def _filters(self, raw: Any, by_name: dict[str, TableInfo]) -> list[FilterSpec]:
        if not isinstance(raw, list) or len(raw) > 16:
            raise ModelPlanError("filters 必须是不超过 16 项的数组")
        result: list[FilterSpec] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ModelPlanError("filter 项必须是对象")
            table = self._identifier(item.get("table"), "filter.table")
            column = self._identifier(item.get("column"), "filter.column")
            info = self._column(by_name, table, column, "filter.column")
            operator = str(item.get("operator", "")).upper().strip()
            value = item.get("value")
            if operator in _RANGE_OPERATORS:
                if not isinstance(value, list) or len(value) != 2 or not all(self._scalar(v) and v is not None for v in value):
                    raise ModelPlanError(f"{operator} 的 value 必须是两个非空标量")
                normalized: Any = tuple(value)
            elif operator in _SET_OPERATORS:
                if not isinstance(value, list) or not 1 <= len(value) <= 50 or not all(self._scalar(v) and v is not None for v in value):
                    raise ModelPlanError(f"{operator} 的 value 必须是 1 到 50 个非空标量")
                normalized = tuple(value)
            elif operator in _OPERATORS:
                if not self._scalar(value):
                    raise ModelPlanError("过滤值必须是标量")
                normalized = value
            else:
                raise ModelPlanError(f"不支持的过滤运算符: {operator}")
            if operator in {">", ">=", "<", "<="} | _RANGE_OPERATORS and not (self._numeric(info.data_type) or self._date(info.name)):
                raise ModelPlanError("大小比较只能作用于数值列或日期列")
            result.append(FilterSpec(column, operator, normalized, str(item.get("source_text") or column), str(item.get("explanation") or "模型过滤"), table))
        return result

    def _having(self, raw: Any, by_name: dict[str, TableInfo], metric_table: str, metric_column: str) -> HavingSpec | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ModelPlanError("having 必须是对象")
        operator = str(raw.get("operator", "")).upper()
        if operator not in {">", ">=", "<", "<="}:
            raise ModelPlanError("having operator 不安全")
        mode = str(raw.get("mode", "literal"))
        if mode == "scalar_avg":
            value = None
        elif mode == "literal" and self._scalar(raw.get("value")):
            value = raw["value"]
        else:
            raise ModelPlanError("having 只支持 literal 或 scalar_avg")
        return HavingSpec(operator, mode, value, str(raw.get("source_text") or "having"), str(raw.get("explanation") or "模型聚合过滤"))

    @staticmethod
    def _path_hint(raw: Any) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            parts = raw.split(">")
        elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            parts = raw
        else:
            raise ModelPlanError("join_path 必须是 'a>b>c' 或表名数组")
        if len(parts) < 2 or any(not part or not part.isidentifier() for part in parts):
            raise ModelPlanError("join_path 含有非法表名")
        return ">".join(parts)

    @staticmethod
    def _identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ModelPlanError(f"{field} 必须是非空标识符")
        # 允许中文和其他 Unicode 标识符，以支持迁移到非英文 Schema；仍拒绝
        # 引号、空白、点号和 SQL 片段，真正的存在性由调用方再次校验。
        if not value.isidentifier():
            raise ModelPlanError(f"{field} 含有非法字符")
        return value

    @staticmethod
    def _label(value: Any, fallback: str) -> str:
        label = str(value or fallback).strip()
        if not label or len(label) > 80 or any(ord(ch) < 32 for ch in label):
            raise ModelPlanError("字段标签非法")
        return label

    @staticmethod
    def _column(by_name: dict[str, TableInfo], table: str, column: str, field: str):
        target = by_name.get(table)
        if target is None or column not in {item.name for item in target.columns}:
            raise ModelPlanError(f"{field} 不存在: {table}.{column}")
        return next(item for item in target.columns if item.name == column)

    @staticmethod
    def _scalar(value: Any) -> bool:
        return value is None or isinstance(value, (str, int, float, bool)) and not isinstance(value, float) or isinstance(value, float) and math.isfinite(value)

    @staticmethod
    def _numeric(data_type: str) -> bool:
        return any(kind in str(data_type).upper() for kind in ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT"))

    @staticmethod
    def _date(column: str) -> bool:
        value = column.lower()
        return value.endswith("date") or value.endswith("time") or value in {"date", "日期", "时间"}

    @staticmethod
    def _limit(value: Any) -> int:
        if isinstance(value, bool):
            raise ModelPlanError("limit 必须是整数")
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ModelPlanError("limit 必须是整数") from exc
        if not 1 <= limit <= 100:
            raise ModelPlanError("limit 必须在 1 到 100 之间")
        return limit

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelPlanError("confidence 必须是数字") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ModelPlanError("confidence 必须在 0 到 1 之间")
        return round(confidence, 3)
