"""NL2SQL 规划、执行和可解释溯源编排（v2）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from . import lexicon
from .model_contract import ModelPlanError, ModelPlanProvider, ModelPlanValidator
from .metric_compiler import MetricCompiler, MetricPlanError
from .models import QueryPlan, QueryResult, TableInfo
from .planner import SingleTablePlanner
from .schema import SchemaIntrospector, SchemaLinker, normalize_text
from .security import SqlSafetyError, execute_read_only
from .semantics import MetricCatalog
from .value_index import ValueIndex

_FOLLOWUP_CUE = re.compile(r"^(那么|那|如果是|如果|换成|改成|再看|再查|同样|也)|呢$|呢[?？]$")


def _reference_from_env() -> date | None:
    raw = os.getenv("ICT8_REFERENCE_DATE", "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class Nl2SqlEngine:
    def __init__(
        self,
        database_path: str | Path,
        *,
        max_rows: int = 100,
        max_steps: int = 50_000_000,
        max_seconds: float | None = None,
        aliases_path: str | Path | None = None,
        model_plan_provider: ModelPlanProvider | None = None,
        model_fallback: bool = True,
        reference_date: date | None = None,
        value_aliases_path: str | Path | None = None,
        model_min_confidence: float = 0.5,
        metric_catalog_path: str | Path | None = None,
    ):
        self.database_path = Path(database_path)
        self.max_rows = max_rows
        if not 50_000 <= max_steps <= 500_000_000:
            raise ValueError("max_steps 必须在 50000 到 500000000 之间")
        self.max_steps = max_steps
        # 墙钟预算是主保护：VM 步数随数据量线性增长，10 万行时合法 JOIN 就会超过旧的 200 万步上限
        self.max_seconds = float(max_seconds if max_seconds is not None else (os.getenv("ICT8_SQL_TIMEOUT", "") or 5.0))
        if not 0.05 <= self.max_seconds <= 120:
            raise ValueError("max_seconds 必须在 0.05 到 120 之间")
        self.introspector = SchemaIntrospector()
        linker = SchemaLinker.from_json(aliases_path) if aliases_path else SchemaLinker()
        self.planner = SingleTablePlanner(linker)
        self.model_plan_provider = model_plan_provider
        self.model_fallback = bool(model_fallback)
        self.model_plan_validator = ModelPlanValidator(max_join_hops=self.planner.max_join_hops)
        self.metric_compiler = MetricCompiler(self.planner)
        catalog_path = metric_catalog_path or os.getenv("ICT8_METRIC_CATALOG", "").strip() or Path(__file__).resolve().parents[2] / "data" / "demo_metric_catalog.json"
        self.metric_catalog = MetricCatalog.from_file(catalog_path) if Path(catalog_path).exists() else None
        # 相对时间的参考日期：显式参数 > ICT8_REFERENCE_DATE > 当天；始终写入假设便于审计
        self.reference_date = reference_date or _reference_from_env() or date.today()
        self.value_aliases_path = value_aliases_path or (os.getenv("ICT8_VALUE_ALIASES", "").strip() or None)
        if not 0.0 <= float(model_min_confidence) <= 1.0:
            raise ValueError("model_min_confidence 必须在 0 到 1 之间")
        self.model_min_confidence = float(model_min_confidence)
        self._snapshot_key: tuple | None = None
        self._snapshot: tuple[tuple[TableInfo, ...], ValueIndex] | None = None
        self._date_storage_cache: dict[tuple[Any, ...], tuple[str, tuple[Any, Any, str]]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.exists():
            raise FileNotFoundError(f"数据库不存在: {self.database_path.name}")
        uri = f"file:{self.database_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
        connection.row_factory = None
        return connection

    def _snapshot_for(self, connection: sqlite3.Connection) -> tuple[tuple[TableInfo, ...], ValueIndex]:
        """Schema 与取值索引按 (文件指纹, schema_version) 缓存，避免每次查询全表扫描。"""

        stat = self.database_path.stat()
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        key = (stat.st_mtime_ns, stat.st_size, schema_version)
        with self._lock:
            if self._snapshot is not None and self._snapshot_key == key:
                return self._snapshot
            self._date_storage_cache.clear()
            tables = self.introspector.introspect(connection, include_row_count=False)
            index = ValueIndex.build(connection, tables, self.value_aliases_path)
            self._snapshot_key, self._snapshot = key, (tables, index)
            return self._snapshot

    def schema(self) -> dict[str, Any]:
        with self._connect() as connection:
            tables = self.introspector.introspect(connection)
        return {"tables": [table.to_dict() for table in tables], "source": "sqlite_read_only"}

    # ------------------------------------------------------------------ planning
    def _rules_plan(self, question: str, tables, connection, index) -> QueryPlan:
        expanded, expansion = self.metric_catalog.expand(question, tables) if self.metric_catalog else (question, None)
        plan = self.planner.plan(
            expanded, tables, connection, value_index=index, reference_date=self.reference_date,
            date_storage_cache=self._date_storage_cache,
            date_storage_cache_namespace=self._snapshot_key,
        )
        return self.metric_catalog.apply(plan, expansion) if self.metric_catalog else plan

    def _model_plan(self, question: str, tables, connection, index) -> QueryPlan:
        try:
            payload = self.model_plan_provider(question, tables)
            plan = self.model_plan_validator.validate(payload, tables, question=question)
            if plan.comparison_mode != "none":
                normalized_period, period_format = self.planner.normalize_comparison_period(
                    plan.comparison_period, tables, connection, plan,
                    date_storage_cache=self._date_storage_cache,
                    date_storage_cache_namespace=self._snapshot_key,
                )
                if normalized_period is None:
                    raise ModelPlanError("模型计划的比较日期字段存储格式无法安全判断")
                plan.comparison_period = normalized_period
                plan.assumptions.append(f"日期参数格式：{period_format}")
            self._check_model_grounding(plan, question, tables, index, connection)
            plan.planner_audit = {
                "candidate_source": "external_model",
                "final_source": "model_validated",
                "fallback": False,
                "decision": "accepted",
            }
            if getattr(self.model_plan_provider, "audit", None):
                plan.planner_audit["provider"] = dict(self.model_plan_provider.audit)
            return plan
        except Exception as exc:  # 任意 provider/校验异常都只影响"提议"，不影响可用性
            if not self.model_fallback:
                raise
            plan = self._rules_plan(question, tables, connection, index)
            plan.planner_source = "rules_fallback"
            rejection_reason = self._safe_model_rejection_reason(exc)
            plan.planner_audit = {
                "candidate_source": "external_model",
                "final_source": "rules_fallback",
                "fallback": True,
                "decision": "rejected_or_unavailable",
                "reason_type": type(exc).__name__,
                "reason_code": self._model_rejection_code(exc),
                "reason": rejection_reason,
            }
            plan.assumptions.append(f"模型计划不可用或未通过校验（{rejection_reason}），已回退规则规划")
            return plan

    @staticmethod
    def _safe_model_rejection_reason(exc: Exception) -> str:
        """限制审计文案，避免异常文本把控制字符或超长内容带入 API。"""

        if not isinstance(exc, ModelPlanError):
            return "provider_unavailable_or_invalid_response"
        return "".join(ch for ch in str(exc) if ord(ch) >= 32 or ch in "\t\n")[:200]

    @staticmethod
    def _model_rejection_code(exc: Exception) -> str:
        """将模型拒绝原因归一为稳定类别，避免评测依赖异常文案。"""

        if not isinstance(exc, ModelPlanError):
            return "provider_unavailable_or_invalid_response"
        message = str(exc)
        rules = (
            ("low_confidence", "置信度"),
            ("ungrounded_metric", "指标与问题中的业务词"),
            ("ungrounded_filter", "过滤值"),
            ("metric_aggregation_mismatch", "指标聚合口径"),
            ("missing_explicit_filter", "明确过滤条件"),
            ("missing_time_range", "明确时间范围"),
            ("extra_having", "增加了问题中没有依据的聚合阈值"),
            ("missing_having", "遗漏了问题中的明确聚合阈值"),
            ("having_mismatch", "聚合阈值与问题不一致"),
            ("extra_dimension", "增加了问题中没有依据的分组维度"),
            ("clarification_bypass", "绕过了明确的分组维度澄清"),
            ("missing_group_dimension", "分组维度"),
            ("time_grain_mismatch", "时间分组粒度"),
            ("comparison_mismatch", "比较时间窗口"),
            ("comparison_mode_mismatch", "同比/环比口径"),
            ("analysis_mode_mismatch", "分析模式"),
            ("top_n_order_mismatch", "Top-N 排序方向"),
            ("top_n_mismatch", "Top-N"),
        )
        for code, marker in rules:
            if marker in message:
                return code
        return "model_contract_or_grounding_rejected"

    def _check_model_grounding(self, plan: QueryPlan, question: str, tables, index: ValueIndex, connection=None) -> None:
        """模型计划的最低可信条件：置信度、来源依据和显式槽位完整性。"""

        if plan.confidence < self.model_min_confidence:
            raise ModelPlanError(f"模型计划置信度 {plan.confidence} 低于门限 {self.model_min_confidence}")
        normalized = normalize_text(question)
        expanded, _ = self.metric_catalog.expand(question, tables) if self.metric_catalog else (question, None)
        rule_metrics = {(link.table, link.column) for link in self.planner.linker.link(expanded, tables) if link.role == "metric"}
        if rule_metrics and (plan.metric_table, plan.metric_column) not in rule_metrics:
            raise ModelPlanError("模型选择的指标与问题中的业务词不一致")
        for item in plan.filters:
            values = item.value if isinstance(item.value, (tuple, list)) else (item.value,)
            for value in values:
                if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    continue
                key = normalize_text(value)
                synonyms = [alias for alias, entries in index.synonyms.items() if any(e.normalized == key for e in entries)]
                if key not in normalized and not any(alias in normalized for alias in synonyms):
                    raise ModelPlanError(f"模型过滤值“{value}”在问题中没有依据")
        for metric in plan.metrics:
            if rule_metrics and (metric.table, metric.column) not in rule_metrics:
                raise ModelPlanError("模型选择的指标与问题中的业务词不一致")
            for item in metric.filters:
                values = item.value if isinstance(item.value, (tuple, list)) else (item.value,)
                for value in values:
                    if normalize_text(str(value)) not in normalized:
                        raise ModelPlanError("模型的指标局部过滤值在问题中没有依据")

        # 反向完整性校验：规则规划器只作为独立的槽位提取器，不能让模型
        # 通过“高置信度”静默丢掉用户明确写出的时间、取值或分组条件。
        # 即使规则规划器最终需要澄清，也要保留它已经安全识别出的槽位；
        # 否则模型可以用高置信度计划绕过“未知维度/未知值/不支持粒度”等门。
        rule_plan = self._rules_plan(question, tables, connection, index)
        if rule_plan.metrics:
            expected = {(m.table, m.column, m.function) for m in rule_plan.metrics}
            actual = {(m.table, m.column, m.function) for m in plan.metrics}
            if expected != actual:
                raise ModelPlanError("模型计划遗漏了问题中的明确指标或聚合口径")
        elif len(plan.metrics) > 1:
            raise ModelPlanError("模型计划增加了问题中没有依据的指标")
        if rule_plan.derived_metrics:
            if ({m.id: m.expression for m in rule_plan.derived_metrics} != {m.id: m.expression for m in plan.derived_metrics}
                    or rule_plan.output_metrics != plan.output_metrics
                    or {m.id: (m.table, m.column, m.function, m.unit, m.currency, m.missing, repr(m.filters)) for m in rule_plan.metrics}
                    != {m.id: (m.table, m.column, m.function, m.unit, m.currency, m.missing, repr(m.filters)) for m in plan.metrics}):
                raise ModelPlanError("模型派生指标必须遵守已确认的业务公式和单位口径")
            plan.semantic_audit = dict(rule_plan.semantic_audit)
        elif plan.derived_metrics:
            raise ModelPlanError("模型不得新增没有业务定义依据的公式")
        # 聚合函数是问题语义的一部分。只校验列名而不校验 SUM/AVG/COUNT
        # 会让模型在高置信度下把“平均值”静默改成“总和”（或反之）。
        if (
            not rule_plan.metrics and rule_plan.metric_function
            and plan.metric_function
            and plan.metric_function != rule_plan.metric_function
        ):
            raise ModelPlanError("模型计划的指标聚合口径与问题不一致")
        if rule_plan.comparison_mode != "none":
            if plan.comparison_mode != rule_plan.comparison_mode:
                raise ModelPlanError("模型计划的同比/环比口径与问题不一致")
            rule_period = rule_plan.comparison_period
            model_period = plan.comparison_period
            period_fields = ("date_table", "date_column", "current_start", "current_end", "previous_start", "previous_end")
            if any(model_period.get(field) != rule_period.get(field) for field in period_fields):
                raise ModelPlanError("模型计划的比较时间窗口与问题不一致")

        model_filters = {
            (item.table or plan.table, item.column, item.operator, repr(item.value))
            for item in plan.filters
        }
        required_filters = {
            (item.table or rule_plan.table, item.column, item.operator, repr(item.value))
            for item in rule_plan.filters
        }
        missing_filters = required_filters - model_filters
        if missing_filters:
            raise ModelPlanError("模型计划遗漏了问题中的明确过滤条件")
        rule_time_filters = [item for item in rule_plan.filters if item.operator in {"RANGE", "BETWEEN"}]
        model_time_filters = [item for item in plan.filters if item.operator in {"RANGE", "BETWEEN"}]
        if rule_time_filters and not model_time_filters and plan.comparison_mode == "none":
            raise ModelPlanError("模型计划遗漏了问题中的明确时间范围")
        if rule_time_filters and model_time_filters:
            expected = {(item.table or rule_plan.table, item.column, repr(item.value)) for item in rule_time_filters}
            actual = {(item.table or plan.table, item.column, repr(item.value)) for item in model_time_filters}
            if not expected.issubset(actual):
                raise ModelPlanError("模型计划的时间范围与问题不一致")
        if rule_plan.dimensions and not set(rule_plan.dimensions).issubset(set(plan.dimensions)):
            raise ModelPlanError("模型计划遗漏了问题中的明确分组维度")
        extras = set(plan.dimensions) - set(rule_plan.dimensions)
        if extras and not self._grounded_dimensions(plan, extras, question, tables):
            # 模型不能把未出现在用户问题中的维度凭空加入结果，避免把总体
            # 聚合静默改变成分组聚合。只有规则层也能从问题中证明的维度才可执行。
            raise ModelPlanError("模型计划增加了问题中没有依据的分组维度")
        required_transforms = {
            column: rule_plan.dimension_transforms.get(column, "raw")
            for column in rule_plan.dimensions
        }
        actual_transforms = {
            column: plan.dimension_transforms.get(column, "raw")
            for column in plan.dimensions
        }
        if any(actual_transforms.get(column) != transform for column, transform in required_transforms.items()):
            raise ModelPlanError("模型计划遗漏了问题中的时间分组粒度")

        # 阈值/高于平均是 HAVING 语义，不能因为模型只返回了维度和指标
        # 就被静默丢掉，也不能凭空增加一个用户没有要求的阈值。
        if rule_plan.having is None and plan.having is not None:
            raise ModelPlanError("模型计划增加了问题中没有依据的聚合阈值")
        if rule_plan.having is not None:
            if plan.having is None:
                raise ModelPlanError("模型计划遗漏了问题中的明确聚合阈值")
            expected_having = (
                rule_plan.having.operator,
                rule_plan.having.mode,
                repr(rule_plan.having.value),
            )
            actual_having = (
                plan.having.operator,
                plan.having.mode,
                repr(plan.having.value),
            )
            if actual_having != expected_having:
                raise ModelPlanError("模型计划的聚合阈值与问题不一致")

        # 分析模式是用户意图的一部分，不能因规则层后续需要澄清而被模型
        # 静默改成总体聚合。仅在规则层能安全识别模式时要求严格一致。
        if rule_plan.analysis_mode != "aggregate" and plan.analysis_mode != rule_plan.analysis_mode:
            raise ModelPlanError("模型计划的分析模式与问题不一致")
        if rule_plan.top_n is not None and plan.top_n != rule_plan.top_n:
            raise ModelPlanError("模型计划的 Top-N 范围与问题不一致")
        if rule_plan.top_n is not None and plan.order_desc != rule_plan.order_desc:
            raise ModelPlanError("模型计划的 Top-N 排序方向与问题不一致")
        if rule_plan.clarification_code == "missing_group_dimension" and not plan.dimensions:
            raise ModelPlanError("模型计划绕过了明确的分组维度澄清")

    def _grounded_dimensions(self, plan, dimensions, question, tables):
        """允许规则没覆盖的表达，但必须有分组意图和实际字段/词典证据。"""
        normalized = normalize_text(question)
        if not re.search(r"按|各|每|分组|\bby\b|\bper\b|group\s+by", question.lower()):
            return False
        rules = self.planner.linker.rules_for(tables)
        for column in dimensions:
            table = plan.dimension_tables.get(column, plan.table)
            aliases = [alias for rule in rules if rule.table == table and rule.column == column and rule.role == "dimension" for alias in rule.aliases]
            aliases += [column, column.replace("_", " ")]
            if not any(normalize_text(alias) in normalized for alias in aliases):
                return False
        return True

    @staticmethod
    def _intent_audit(plan: QueryPlan, question: str) -> dict[str, Any]:
        """生成执行前意图卡；只描述已验证计划，不重新解释自然语言。"""

        filters = [item.to_dict() for item in plan.filters]
        unresolved = list(plan.coverage.get("unresolved", []))
        blocking = bool(plan.clarification or unresolved)
        comparison_window = None
        if plan.comparison_mode != "none" and plan.comparison_period:
            period = plan.comparison_period
            comparison_window = {
                "date_table": period.get("date_table"),
                "date_column": period.get("date_column"),
                "current": [period.get("current_start_text", period.get("current_start")),
                            period.get("current_end_text", period.get("current_end"))],
                "previous": [period.get("previous_start_text", period.get("previous_start")),
                             period.get("previous_end_text", period.get("previous_end"))],
            }
        audit = {
            "question": question,
            "metric": {
                "table": plan.metric_table,
                "column": plan.metric_column,
                "function": plan.metric_function,
                "label": plan.metric_label,
            },
            "filters": filters,
            "time_range": next(
                (list(item.value) for item in plan.filters if item.operator in {"RANGE", "BETWEEN"}
                 and isinstance(item.value, (tuple, list))),
                comparison_window["current"] if comparison_window else None,
            ),
            "group_by": [
                {
                    "table": plan.dimension_tables.get(column, plan.table),
                    "column": column,
                    "grain": plan.dimension_transforms.get(column, "raw"),
                    "label": plan.dimension_labels.get(column, column),
                }
                for column in plan.dimensions
            ],
            "comparison": plan.comparison_mode,
            "comparison_window": comparison_window,
            "top_n": plan.top_n,
            "confidence": plan.confidence,
            "unresolved": unresolved,
            "execution_allowed": not blocking,
            "planner_source": plan.planner_source,
            "planner_audit": plan.planner_audit,
            "metrics": [asdict(m) for m in plan.metrics],
            "derived_metrics": [asdict(m) for m in plan.derived_metrics],
            "grain_audit": plan.grain_audit,
            "semantic_audit": plan.semantic_audit,
        }
        return audit

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, *, max_rows: int | None = None) -> QueryResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 不能为空")
        row_cap = int(max_rows or self.max_rows)
        with self._connect() as connection:
            tables, index = self._snapshot_for(connection)
            if self.model_plan_provider is None:
                plan = self._rules_plan(question, tables, connection, index)
            else:
                plan = self._model_plan(question, tables, connection, index)
            compiled = None
            if not plan.clarification and (plan.metrics or plan.fan_out):
                try:
                    compiled = self.metric_compiler.compile(plan, tables)
                except MetricPlanError as exc:
                    plan.clarification, plan.clarification_code = str(exc), exc.code
                    plan.confidence = 0.3
            plan.intent_audit = self._intent_audit(plan, question)
            if plan.clarification:
                return QueryResult(
                    status="clarification", question=question, rewritten_question=plan.rewritten_question,
                    sql=None, parameters=(), columns=(), rows=(), plan=plan.to_dict(),
                    explanation=(plan.clarification,),
                    provenance={"source_type": "structured_database", "database": self.database_path.name},
                    clarification=plan.clarification, clarification_code=plan.clarification_code,
                    clarification_options=tuple(plan.clarification_options),
                )
            sql, parameters = compiled or self.planner.build_sql(plan)
            columns, rows = execute_read_only(connection, sql, parameters, max_rows=row_cap, max_steps=self.max_steps, max_seconds=self.max_seconds)
            result_state, notices = self._result_state(connection, plan, rows)
        query_hash = hashlib.sha256(
            json.dumps({"sql": sql, "parameters": parameters}, ensure_ascii=False, default=str, sort_keys=True).encode()
        ).hexdigest()[:16]
        explanation = [
            f"识别指标：{plan.metric_label}（{plan.metric_function}）",
            f"查询表：{plan.table}",
            *([f"分析模式：{plan.analysis_mode}"] if plan.analysis_mode != "aggregate" else []),
            *([f"Top-N：前 {plan.top_n}（DENSE_RANK，含并列）"] if plan.top_n else []),
            *([f"比较模式：{plan.comparison_mode}"] if plan.comparison_mode != "none" else []),
            *(item.explanation for item in plan.filters),
            *([f"聚合阈值：{plan.having.explanation}"] if plan.having else []),
            *plan.assumptions,
            *notices,
            "执行策略：只读参数化 SQL，并通过 SQLite authorizer 拒绝写操作。",
        ]
        provenance = {
            "source_type": "structured_database",
            "database": self.database_path.name,
            "table": plan.table,
            "row_count": len(rows),
            "query_hash": query_hash,
            "field_links": [item.to_dict() for item in plan.links],
            "coverage": plan.coverage,
            "grain_audit": plan.grain_audit,
            "result_cells": [
                {"row": i, "column": column, "query_hash": query_hash,
                 "locator": {"type": "sql_result", "row": i, "column": column}}
                for i, row in enumerate(rows) for column in row
            ],
        }
        return QueryResult(
            status="ok", question=question, rewritten_question=plan.rewritten_question, sql=sql,
            parameters=parameters, columns=columns, rows=rows, plan=plan.to_dict(),
            explanation=tuple(explanation), provenance=provenance, result_state=result_state, notices=tuple(notices),
        )

    def _result_state(self, connection, plan: QueryPlan, rows) -> tuple[str, list[str]]:
        empty = not rows or (not plan.dimensions and all(value is None for value in rows[0].values()))
        if not empty:
            return "rows", []
        date_ref = None
        if plan.comparison_mode != "none":
            date_ref = (plan.comparison_period["date_table"], plan.comparison_period["date_column"])
        else:
            date_ref = next(((f.table or plan.table, f.column) for f in plan.filters if f.operator in {"RANGE", "BETWEEN"}), None)
        if date_ref is None:
            return "empty", ["查询条件下没有匹配数据"]
        try:
            _, coverage = execute_read_only(
                connection,
                f'SELECT MIN("{date_ref[1]}") AS min_value, MAX("{date_ref[1]}") AS max_value FROM "{date_ref[0]}"',
                (), max_rows=1, max_steps=self.max_steps, max_seconds=self.max_seconds,
            )
            low, high = coverage[0]["min_value"], coverage[0]["max_value"]
            return "empty", [f"该时间范围内没有数据；{date_ref[0]}.{date_ref[1]} 的数据覆盖 {low} 至 {high}"]
        except SqlSafetyError:
            return "empty", ["该时间范围内没有数据"]

    # ------------------------------------------------------------------ multi-turn
    def analyze_slots(self, question: str) -> dict[str, Any]:
        with self._connect() as connection:
            tables, index = self._snapshot_for(connection)
        normalized = normalize_text(question)
        links = self.planner.linker.link(question, tables)
        time_parse = lexicon.parse_time(normalized, self.reference_date)
        matches, _ = index.match(normalized, {(link.table, link.column) for link in links})
        return {
            "normalized": normalized,
            "metrics": [link for link in links if link.role == "metric"],
            "dimensions": [link for link in links if link.role == "dimension"],
            "values": matches,
            "time_spans": time_parse.spans,
        }

    def contextualize(self, previous: str, current: str) -> tuple[str, dict[str, Any]]:
        """把追问改写为独立问题（槽位级继承/覆盖），代替原来的字符串拼接。

        规则：本轮同时给出指标和(时间/取值/维度)且没有追问语气 → 独立问题；
        否则以上一轮的独立问题为底稿，按槽位覆盖：同列取值替换、时间替换、
        指标替换，其余补充语（如"按月统计"）追加在末尾。
        """

        previous = (previous or "").strip()
        current = (current or "").strip()
        if not previous or not current or previous == current:
            return current, {"mode": "none"}
        prev, cur = self.analyze_slots(previous), self.analyze_slots(current)
        followup = bool(_FOLLOWUP_CUE.search(cur["normalized"]))
        has_content = cur["metrics"] or cur["dimensions"] or cur["values"] or cur["time_spans"]
        if not has_content and not followup:
            return current, {"mode": "independent", "reason": "no_structured_slots"}
        if cur["metrics"] and (cur["time_spans"] or cur["values"] or cur["dimensions"]) and not followup:
            return current, {"mode": "independent", "reason": "self_contained"}

        merged = prev["normalized"]
        remainder = cur["normalized"]
        replaced: list[dict[str, str]] = []
        # 同一列的值是一个集合槽位：本轮出现该列时，整体替换上一轮
        # 的并列值，而不是每次只替换旧集合的第一个元素。
        current_groups: dict[tuple[str, str], list[Any]] = {}
        previous_groups: dict[tuple[str, str], list[Any]] = {}
        for value in cur["values"]:
            current_groups.setdefault((value.table, value.column), []).append(value)
        for value in prev["values"]:
            previous_groups.setdefault((value.table, value.column), []).append(value)
        for slot, values in current_groups.items():
            old_values = previous_groups.get(slot, [])
            if old_values:
                old_text = "和".join(item.span for item in old_values)
                new_text = "和".join(item.span for item in values)
                merged = merged.replace(old_text, new_text, 1)
                # 兼容中文顿号/逗号等原始并列连接形式。
                for separator in ("、", "，", ",", "与", "及"):
                    old_text = separator.join(item.span for item in old_values)
                    merged = merged.replace(old_text, new_text, 1)
                replaced.append({"slot": f"{slot[0]}.{slot[1]}", "from": "、".join(item.span for item in old_values), "to": "、".join(item.span for item in values)})
            for value in values:
                remainder = remainder.replace(value.span, "", 1)
        if cur["time_spans"]:
            if prev["time_spans"]:
                merged = merged.replace(prev["time_spans"][0], cur["time_spans"][0], 1)
                for extra in prev["time_spans"][1:]:
                    merged = merged.replace(extra, "", 1)
                replaced.append({"slot": "time", "from": prev["time_spans"][0], "to": cur["time_spans"][0]})
            else:
                merged = cur["time_spans"][0] + merged
                replaced.append({"slot": "time", "from": "", "to": cur["time_spans"][0]})
            for span in cur["time_spans"]:
                remainder = remainder.replace(span, "", 1)
        if cur["metrics"]:
            new_alias = normalize_text(cur["metrics"][0].matched_alias)
            if prev["metrics"]:
                old_alias = normalize_text(prev["metrics"][0].matched_alias)
                merged = merged.replace(old_alias, new_alias, 1)
                replaced.append({"slot": "metric", "from": old_alias, "to": new_alias})
            else:
                merged += new_alias
            remainder = remainder.replace(new_alias, "", 1)
        prev_dimension_columns = {(link.table, link.column) for link in prev["dimensions"]}
        for link in cur["dimensions"]:
            if (link.table, link.column) in prev_dimension_columns and not re.search(r"按|各|每|分别", remainder):
                remainder = remainder.replace(normalize_text(link.matched_alias), "", 1)
        for word in sorted(lexicon.FILLER_WORDS, key=len, reverse=True):
            remainder = remainder.replace(normalize_text(word), "")
        # 槽位替换可能留下并列连接词或否定修饰词；它们没有独立查询语义，
        # 必须从改写问题中移除，避免下一轮解析把残片当成新约束。
        remainder = re.sub(r"(?:^|\s)(?:和|与|及|、|，|,)(?=\s|$)", " ", remainder)
        remainder = re.sub(r"(?:只)?(?:排除|不含|不包括|除了)(?=\s|$)", " ", remainder)
        remainder = remainder.strip("的 ")
        if remainder:
            merged = f"{merged} {remainder}"
        return merged, {"mode": "merged", "base": previous, "replaced": replaced, "appended": remainder}
