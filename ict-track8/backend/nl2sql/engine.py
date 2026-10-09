"""NL2SQL 规划、执行和可解释溯源编排（v2）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, replace
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from . import lexicon
from .model_contract import ModelPlanError, ModelPlanProvider, ModelPlanValidator
from .metric_compiler import MetricCompiler, MetricPlanError
from .models import QueryPlan, QueryResult, TableInfo, LinkCandidate
from .planner import SingleTablePlanner, _strip_unsafe_instruction_noise
from .schema import SchemaIntrospector, SchemaLinker, normalize_text
from .security import SqlSafetyError, execute_read_only
from .semantics import MetricCatalog
from .value_index import ValueIndex
from .semantic_graph import SemanticGraph, needs_normalization
from .plan_structure import canonicalize, diagnose
from .question_roles import association_scope, binding_present, group_fields, group_grains
from .result_scope import configure_complete_scope, requests_complete_result, scalar_aggregate_cardinality
from .result_artifact import (ResultBudgets, execute_complete_read_only,
                              pin_database, read_result_page)

_FOLLOWUP_CUE = re.compile(r"^(那么|那|如果是|如果|换成|改成|再看|再查|同样|也)|呢$|呢[?？]$")


_SAFETY_REASON_CODES = frozenset({
    'complete_result_operation_time_budget_exceeded',
    'complete_result_source_byte_budget_exceeded',
    'complete_result_source_time_budget_exceeded',
    'complete_result_source_snapshot_changed',
    'complete_result_source_changed_after_planning',
    'complete_result_source_or_producer_changed',
    'unsupported_sqlite_result_cell',
    'complete_result_negative_scope_ambiguous_or_unsupported',
    'complete_result_preview_scope_ambiguous_or_unsupported',
    'complete_result_positive_negative_cap_conflict',
    'complete_result_scope_ambiguous_or_unsupported',
    'complete_result_row_cap_conflicts_with_ties',
    'result_semantic_row_limit_invalid',
    'relational_unverified_result_offset',
    'relational_result_limit_not_user_scope',
    'result_artifact_initial_binding_mismatch',
    'complex_query_ast_budget', 'complex_query_recursive_with',
    'complex_query_model_placeholder', 'complex_query_unknown_or_ambiguous_field',
    'complex_query_source_not_allowed', 'complex_query_no_physical_source',
    'complex_query_unverified_join', 'complex_query_join_not_actual_fk_or_shared_key',
    'complex_query_unbound_correlation', 'complex_query_unverified_correlation',
    'complex_query_duplicate_or_unbounded_outputs',
    'complex_query_integer_literal_out_of_range', 'complex_query_nonfinite_literal',
    'complex_query_parameter_binding_mismatch', 'complex_query_proposal_contract',
    'complex_query_explicit_nonnull_count_requires_field_projection',
    'complex_query_record_count_requires_row_projection',
    'complex_query_explicit_group_source_mismatch',
    'complex_query_null_row_count_requires_row_projection',
    'complex_query_null_row_count_requires_null_predicate',
    'complex_query_null_and_nonnull_count_scope_requires_separate_query',
    'complex_query_verified_date_requires_julianday',
})
_SAFETY_MESSAGE_CODES = {
    'SQL 不能为空': 'sql_empty',
    'SQL 不允许注释或 NUL 字符': 'sql_comments_or_nul',
    'SQL 超过长度上限（100000 字符）': 'sql_length_budget_exceeded',
    'SQL 无法按 SQLite 方言解析': 'sql_parse_failed',
    '只允许执行一条 SQL': 'sql_multiple_statements',
    '只允许 SELECT 或只读 WITH 查询': 'sql_non_read_only_query',
    'SQL 包含禁止的写入或管理操作': 'sql_write_or_management_operation',
    'SQL 包含禁止的扩展或文件写入函数': 'sql_forbidden_function',
    'max_rows 必须在 1 到 1000 之间': 'sql_row_budget_invalid',
    '查询结果超过安全行数上限': 'sql_row_budget_exceeded',
}


def _safe_failure_diagnostics(exc: SqlSafetyError, stage: str) -> dict[str, str]:
    """Fixed diagnostic vocabulary; never copy database/model error text.

    SQLite INTERRUPT alone cannot distinguish a step from a wall-clock limit,
    so its code deliberately preserves that uncertainty.
    """
    message = str(exc)
    code = message if message in _SAFETY_REASON_CODES else _SAFETY_MESSAGE_CODES.get(message)
    if code is None and message.startswith('complex_query_function_not_allowed:'):
        code = 'complex_query_function_not_allowed'
    if code is None and isinstance(exc.__cause__, sqlite3.DatabaseError):
        sqlite_code = getattr(exc.__cause__, 'sqlite_errorcode', None)
        code = {
            sqlite3.SQLITE_INTERRUPT: 'sql_execution_interrupted_budget_or_cancel',
            sqlite3.SQLITE_AUTH: 'sql_execution_authorization_denied',
            sqlite3.SQLITE_TOOBIG: 'sql_execution_cell_too_large',
        }.get(sqlite_code, 'sql_execution_database_error')
    return {'stage': stage, 'code': code or 'sql_safety_rejected'}


@contextmanager
def _safety_stage(stage: str):
    """Annotate and re-raise the same refusal, without changing its message."""
    try:
        yield
    except SqlSafetyError as exc:
        diagnostics = _safe_failure_diagnostics(exc, stage)
        exc.safety_stage = diagnostics['stage']
        exc.safety_code = diagnostics['code']
        exc.safety_diagnostics = diagnostics
        raise


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
        result_artifact_dir: str | Path | None = None,
        complete_result_budgets: ResultBudgets | None = None,
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
        # An external schema's explicit annotations do not implicitly opt in
        # to additional aliases from this application's demonstration catalog.
        language_catalog = self.metric_catalog if (not aliases_path or metric_catalog_path
            or os.getenv('ICT8_METRIC_CATALOG', '').strip()) else None
        self.planner.linker.set_metric_catalog(language_catalog)
        self.semantic_graph = SemanticGraph(language_catalog)
        self._semantic_normalizations = OrderedDict()
        self._semantic_rules_tables = None
        self._semantic_rules = ()
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
        self._read_scope = threading.local()
        self.result_artifact_dir = (Path(result_artifact_dir) if result_artifact_dir else
            Path(__file__).resolve().parents[3] / 'runtime' / 'query-results')
        self.complete_result_budgets = complete_result_budgets or ResultBudgets(
            max_seconds=self.max_seconds, max_steps=self.max_steps)
        self._result_bindings = OrderedDict()

    @contextmanager
    def consistent_reads(self):
        """Lazy, thread-local read transaction across several answer calls.

        SQL is opened on first use, so document-only plans never require a DB.
        Nested scopes borrow the owner; only the outer scope closes it.
        """
        if getattr(self._read_scope, 'current', None) is not None:
            yield
            return
        scope = {'connection': None, 'snapshot': None}
        self._read_scope.current = scope
        try:
            yield
        finally:
            try:
                if scope['connection'] is not None:
                    scope['connection'].close()
            finally:
                del self._read_scope.current

    # ------------------------------------------------------------------ infra
    @contextmanager
    def _connect(self):
        scope = getattr(self._read_scope, 'current', None)
        if scope is not None and scope['connection'] is not None:
            yield scope['connection']
            return
        if not self.database_path.exists():
            raise FileNotFoundError(f"数据库不存在: {self.database_path.name}")
        # Reserved URI characters in a legitimate filename must not become a
        # fragment/query and accidentally drop mode=ro or open another file.
        uri = self.database_path.resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
        connection.row_factory = None
        try:
            connection.execute('BEGIN')
            if scope is not None:
                scope['connection'] = connection
                self._snapshot_for(connection)
            yield connection
        finally:
            if scope is None:
                connection.close()

    def _source_revision(self):
        """Conservative generation invalidation, including uncheckpointed WAL.

        This is a cache generation, not a cryptographic hash of all DB rows.
        Alias files are small; hash their contents so preserved mtimes cannot
        keep an obsolete business mapping in the entity-value index.
        """
        def state(path, *, optional=False):
            try:
                stat = path.stat()
                return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except FileNotFoundError:
                if optional:
                    return None
                raise
        aliases = None
        if self.value_aliases_path:
            path = Path(self.value_aliases_path)
            aliases = hashlib.sha256(path.read_bytes()).hexdigest()
        actual = self.database_path.resolve()
        return (state(actual), state(Path(str(actual)+'-wal'),optional=True), aliases)

    def current_source_revision(self):
        """Current read-snapshot generation, using the response provenance format."""
        with self._connect() as connection:
            _, _, revision = self._snapshot_for(connection)
            return hashlib.sha256(repr(revision).encode()).hexdigest()

    def _snapshot_for(self, connection: sqlite3.Connection):
        """Pin one read snapshot for Schema, values, date inference and SQL.

        Only publish a reusable cache if the file generation stayed stable
        across both acquiring the snapshot and building its index.
        """
        scope = getattr(self._read_scope, 'current', None)
        if scope is not None and scope['connection'] is connection and scope['snapshot'] is not None:
            return scope['snapshot']
        def pinned(snapshot):
            if scope is not None and scope['connection'] is connection:
                scope['snapshot'] = snapshot
            return snapshot
        for _attempt in range(3):
            before = self._source_revision()
            schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
            if before == self._source_revision():
                break
            connection.rollback()
            connection.execute('BEGIN')
        else:
            raise sqlite3.OperationalError('数据库持续更新，暂不能建立一致的查询快照')
        key = (before, schema_version)
        with self._lock:
            if self._snapshot is not None and self._snapshot_key == key:
                return pinned((*self._snapshot, key))
            self._date_storage_cache.clear()
            tables = self.introspector.introspect(connection, include_row_count=False)
            index = ValueIndex.build(connection, tables, self.value_aliases_path)
            if before == self._source_revision():
                self._snapshot_key, self._snapshot = key, (tables, index)
            return pinned((tables, index, key))

    def schema(self, *, include_row_count: bool = True) -> dict[str, Any]:
        with self._connect() as connection:
            tables = self.introspector.introspect(connection,include_row_count=include_row_count)
        return {"tables": [table.to_dict() for table in tables], "source": "sqlite_read_only"}

    # ------------------------------------------------------------------ planning
    def normalize_question(self, question, *, history_hint=None):
        """Resolve colloquial concepts locally against the current real schema.

        Old, already annotated questions skip the database entirely here.
        Cached results are tied to source generation and the trusted hint;
        changing schema, values or conversation scope cannot reuse a binding.
        """
        if not needs_normalization(question):
            return None
        with self._connect() as connection:
            tables, index, revision = self._snapshot_for(connection)
            return self._normalize_in_snapshot(question, tables, index, revision, history_hint)

    def _normalize_in_snapshot(self, question, tables, index, revision, history_hint=None):
        if not needs_normalization(question):
            return None
        hint_key = json.dumps(history_hint or {}, ensure_ascii=False, sort_keys=True)
        key = (question, revision, hint_key, getattr(self.semantic_graph.metric_catalog, 'digest', None))
        with self._lock:
            cached = self._semantic_normalizations.get(key)
            if cached is not None:
                self._semantic_normalizations.move_to_end(key)
                return cached
            if self._semantic_rules_tables is not tables:
                self._semantic_rules = self.planner.linker.rules_for(tables)
                self._semantic_rules_tables = tables
            rules = self._semantic_rules
        # Only values occurring in this question need literal protection.
        text = normalize_text(question)
        values = tuple(entry.value for token, entries in index.by_normalized.items()
                       if token in text for entry in entries)
        normalized = self.semantic_graph.normalize(question, tables, rules,
            history_hint=history_hint, protected_values=values)
        with self._lock:
            self._semantic_normalizations[key] = normalized
            while len(self._semantic_normalizations) > 256:
                self._semantic_normalizations.popitem(last=False)
        return normalized

    def _rules_plan(self, question: str, tables, connection, index, cache_namespace=None) -> QueryPlan:
        semantic = self._normalize_in_snapshot(question, tables, index, cache_namespace)
        if semantic is not None and semantic.ambiguities:
            return QueryPlan(rewritten_question=question, clarification=semantic.clarification,
                clarification_code='ambiguous_business_expression',
                semantic_audit={'language_normalization': semantic.to_dict()})
        if semantic is not None:
            question = semantic.normalized_question
        expanded, expansion = self.metric_catalog.expand(question, tables) if self.metric_catalog else (question, None)
        if expansion and self._catalog_expansion_omits_an_explicit_metric(question, tables, expansion):
            expansion = None
        metric_override = None
        date_binding_override = None
        if (self.metric_catalog and expansion and not expansion.get("calculations")
                and len(expansion.get("sources", ())) == 1 and expansion.get("source_alias")):
            metric_id = expansion["sources"][0]
            metric = self.metric_catalog.sources[metric_id]
            metric_override = {"table": metric.table, "column": metric.column,
                               "function": metric.function, "label": metric.label,
                               "alias": expansion["source_alias"]}
            time_column = self.metric_catalog.time_columns.get(metric_id)
            if time_column and any(table.name == metric.table and
                                   time_column in {column.name for column in table.columns}
                                   for table in tables):
                date_binding_override = (metric.table, time_column)
        if expansion and self.metric_catalog:
            source_ids = expansion.get("sources", ())
            bindings = []
            for metric_id in source_ids:
                metric = self.metric_catalog.sources.get(metric_id)
                time_column = self.metric_catalog.time_columns.get(metric_id)
                if (metric is None or not time_column or not any(
                        table.name == metric.table and time_column in {column.name for column in table.columns}
                        for table in tables)):
                    bindings = []
                    break
                bindings.append((metric.table, time_column))
            if bindings and len(set(bindings)) == 1:
                # Every requested measure declares the same temporal role, so
                # a shared year/month filter has one catalog-verified meaning.
                date_binding_override = bindings[0]
        if date_binding_override is None:
            date_binding_override = self._shared_catalog_time_binding(expanded, tables)
        plan = self.planner.plan(
            expanded, tables, connection, value_index=index, reference_date=self.reference_date,
            date_storage_cache=self._date_storage_cache,
            date_storage_cache_namespace=cache_namespace,
            metric_override=metric_override,
            date_binding_override=date_binding_override,
        )
        plan = self._remove_implicit_derived_source_groups(plan, question, expansion)
        if plan.clarification and metric_override:
            metric_id = expansion["sources"][0]
            plan.semantic_audit = {**plan.semantic_audit,
                "catalog_version": self.metric_catalog.version,
                "catalog_sha256": self.metric_catalog.digest,
                "sources": [metric_id], "calculations": [], "outputs": [metric_id],
                "source_alias": metric_override["alias"],
            }
        plan = self.metric_catalog.apply(plan, expansion) if self.metric_catalog else plan
        if (not plan.clarification and expansion and not expansion.get("calculations")
                and len(expansion.get("sources", ())) == 1
                and re.search(r"不同|去重|不重复|distinct", question, re.I)):
            metric_id = expansion["sources"][0]
            source_metric = self.metric_catalog.sources.get(metric_id)
            if source_metric and source_metric.function == "COUNT":
                for metric in plan.metrics:
                    if metric.id == metric_id:
                        metric.function = "COUNT_DISTINCT"
                if (plan.metric_table, plan.metric_column) == (source_metric.table, source_metric.column):
                    plan.metric_function = "COUNT_DISTINCT"
        if semantic is not None and semantic.changed:
            plan.semantic_audit['language_normalization'] = semantic.to_dict()
        return plan

    def _shared_catalog_time_binding(self, question, tables):
        """Resolve one shared date role only for explicitly conjoined catalog metrics."""
        if not self.metric_catalog:
            return None
        normalized = normalize_text(question)
        links = [link for link in self.planner.linker.link(question, tables) if link.role == 'metric']
        best = {}
        for link in links:
            alias = normalize_text(link.matched_alias)
            if not alias:
                continue
            if alias not in best or link.score > best[alias][0]:
                best[alias] = (link.score, {(link.table, link.column, link.metric_function)}, link)
            elif link.score == best[alias][0]:
                best[alias][1].add((link.table, link.column, link.metric_function))
        ordered = []
        for score, owners, link in best.values():
            if len({(table, column) for table, column, _ in owners}) != 1:
                return None
            ordered.append(link)
        ordered.sort(key=lambda link: normalized.find(normalize_text(link.matched_alias)))
        selected = []
        seen = set()
        for link in ordered:
            pair = (link.table, link.column, link.metric_function)
            if pair not in seen:
                seen.add(pair)
                selected.append(link)
        if len(selected) < 2:
            return None
        for left, right in zip(selected, selected[1:]):
            start = normalized.find(normalize_text(left.matched_alias)) + len(normalize_text(left.matched_alias))
            end = normalized.find(normalize_text(right.matched_alias), start)
            if end < start or not re.search(r'(?:和|及|与|、|同时|以及|,)', normalized[start:end]):
                return None
        bindings = []
        for link in selected:
            candidates = [metric_id for metric_id, metric in self.metric_catalog.sources.items()
                if (metric.table, metric.column) == (link.table, link.column)
                and (link.metric_function is None or metric.function == link.metric_function)
                and self.metric_catalog.time_columns.get(metric_id)]
            candidate_bindings = {(self.metric_catalog.sources[metric_id].table,
                                   self.metric_catalog.time_columns[metric_id])
                                  for metric_id in candidates}
            if len(candidate_bindings) != 1:
                return None
            bindings.append(next(iter(candidate_bindings)))
        if len(set(bindings)) != 1:
            return None
        table_name, column_name = bindings[0]
        if not any(table.name == table_name and column_name in {column.name for column in table.columns}
                   for table in tables):
            return None
        return table_name, column_name

    def _remove_implicit_derived_source_groups(self, plan, question, expansion):
        """Keep derived inputs as measures unless the original asks to group by one."""
        if (not expansion or not expansion.get("calculations") or plan.clarification
                or not self.metric_catalog):
            return plan
        source_pairs = {(self.metric_catalog.sources[metric_id].table,
                         self.metric_catalog.sources[metric_id].column)
                        for metric_id in expansion.get("sources", ())}
        normalized = normalize_text(question)
        group_scopes = [normalize_text(match.group(1)) for match in re.finditer(
            r'(?<!不)按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总)', question)]
        for metric_id in expansion.get("calculations", ()):
            for alias in self.metric_catalog.aliases.get(metric_id, ()):
                for occurrence in re.finditer(re.escape(alias), normalized):
                    prefix = normalized[:occurrence.start()]
                    marker = re.search(r'(?:各|每个|每一)([^,;。?!？；]{0,80})$', prefix)
                    if marker:
                        group_scopes.append(marker.group(1))
        removed = set()
        kept = []
        for column in plan.dimensions:
            pair = (plan.dimension_tables.get(column, plan.table), column)
            if pair not in source_pairs:
                kept.append(column)
                continue
            metric_ids = [metric_id for metric_id in expansion.get("sources", ())
                if (self.metric_catalog.sources[metric_id].table,
                    self.metric_catalog.sources[metric_id].column) == pair]
            explicitly_grouped = any(
                alias in scope
                for metric_id in metric_ids
                for alias in self.metric_catalog.aliases.get(metric_id, ())
                for scope in group_scopes
            )
            if explicitly_grouped:
                kept.append(column)
            else:
                removed.add(pair)
        if not removed:
            return plan
        plan.dimensions = kept
        for column in {pair[1] for pair in removed}:
            plan.dimension_tables.pop(column, None)
            plan.dimension_transforms.pop(column, None)
            plan.dimension_labels.pop(column, None)
        plan.links = [replace(link, role="metric")
                      if (link.table, link.column) in removed and link.role == "dimension"
                      else link for link in plan.links]
        return plan

    def _catalog_expansion_omits_an_explicit_metric(self, question, tables, expansion):
        """Do not let one catalog alias erase a second, explicitly linked metric."""
        if (not self.metric_catalog or expansion.get("calculations")
                or len(expansion.get("sources", ())) != 1):
            return False
        if not re.search(r"和|与|及|以及|、|并且|同时|加上|[,，]", question):
            return False
        metric = self.metric_catalog.sources.get(expansion["sources"][0])
        selected_alias = normalize_text(expansion.get("source_alias", ""))
        if metric is None or not selected_alias:
            return False
        normalized = normalize_text(question)
        selected_pair = (metric.table, metric.column)
        selected_spans = [match.span() for match in re.finditer(re.escape(selected_alias), normalized)]
        if not selected_spans:
            return False
        separators = re.compile(r"和|与|及|以及|、|并且|同时|加上|[,，]")
        order_value_spans = [match.span(1) for match in re.finditer(
            r'(?:并)?按(总金额|金额|数额|指标值)(?:从高到低|从低到高|高到低|低到高|'
            r'由高到低|由低到高|降序|升序)', normalized)]
        for link in self.planner.linker.link(question, tables):
            pair = (link.table, link.column)
            alias = normalize_text(link.matched_alias)
            if link.role != "metric" or pair == selected_pair or not alias or alias == selected_alias:
                continue
            for match in re.finditer(re.escape(alias), normalized):
                start, end = match.span()
                if (alias in {"金额", "总金额", "数额", "指标值"}
                        and any(order_start <= start and end <= order_end
                                for order_start, order_end in order_value_spans)):
                    continue
                for selected_start, selected_end in selected_spans:
                    if start < selected_end and selected_start < end:
                        continue
                    between = normalized[min(end, selected_end):max(start, selected_start)]
                    if separators.search(between):
                        return True
        return False

    def _fast_sql_plan_eligible(self, plan, question, tables=None):
        top_n_safe = (
            plan.top_n is None
            or (isinstance(plan.top_n, int) and not isinstance(plan.top_n, bool)
                and 1 <= plan.top_n <= 100 and len(plan.dimensions) == 1
                and len(plan.metrics) == 1 and not plan.derived_metrics and plan.having is None)
        )
        analysis_shape_safe = (
            plan.analysis_mode == "aggregate"
            or (plan.analysis_mode == "rank" and len(plan.dimensions) == 1
                and len(plan.metrics) == 1 and not plan.derived_metrics and plan.having is None)
        )
        catalog_sources_safe = bool(plan.metrics) and all(
            metric.id in getattr(self.metric_catalog, "sources", {})
            and (metric.table, metric.column, metric.function, metric.label) == (
                self.metric_catalog.sources[metric.id].table,
                self.metric_catalog.sources[metric.id].column,
                self.metric_catalog.sources[metric.id].function,
                self.metric_catalog.sources[metric.id].label,
            )
            for metric in plan.metrics
        ) if self.metric_catalog else False
        catalog_derived_safe = all(
            metric.id in getattr(self.metric_catalog, "derived", {})
            and metric.label == self.metric_catalog.derived[metric.id].label
            and metric.expression == self.metric_catalog.derived[metric.id].expression
            for metric in plan.derived_metrics
        ) if self.metric_catalog else False
        catalog_formula_safe = catalog_sources_safe and catalog_derived_safe
        having_safe = (plan.having is None or (
            plan.having.mode in {"literal", "scalar"}
            and plan.having.operator in {">", ">=", "<", "<=", "=", "!="}
            and isinstance(plan.having.value, (int, float))
            and not isinstance(plan.having.value, bool)
            and len(plan.metrics) <= 1
            and not plan.derived_metrics
        ))
        bindings = plan.semantic_audit.get("bindings", ())
        catalog_binding_complete = (bool(plan.metrics) and isinstance(bindings, list)
            and len(bindings) == len(plan.metrics)
            and plan.semantic_audit.get("catalog_sha256") == getattr(self.metric_catalog, "digest", None))
        catalog_metric = bool(plan.semantic_audit.get("source_alias")
            and plan.semantic_audit.get("catalog_sha256") == getattr(self.metric_catalog, "digest", None))
        catalog_calculation = bool(plan.semantic_audit.get("calculations")
            and plan.semantic_audit.get("catalog_sha256") == getattr(self.metric_catalog, "digest", None))
        if (catalog_metric or catalog_calculation) and plan.clarification:
            return plan.clarification_code in {"unsupported_date_precision", "ambiguous_aggregation"}
        if catalog_metric or catalog_calculation:
            source_tables = {metric.table for metric in plan.metrics}
            safe_shape = (not plan.clarification and catalog_formula_safe and len(source_tables) == 1
                and analysis_shape_safe and top_n_safe
                and plan.comparison_mode == "none"
                and not plan.dimension_transforms and len(plan.dimensions) <= 1
                and bool(plan.coverage) and not plan.coverage.get("unresolved")
                and not plan.join_tables and not plan.join_conditions and not plan.join_path
                and not plan.fan_out and having_safe)
            if not safe_shape:
                return False
            if tables is None:
                return False
            try:
                MetricCompiler(self.planner).compile(plan, tables)
            except MetricPlanError:
                return False
            except (TypeError, ValueError):
                return False
            return True
        if catalog_binding_complete and len(plan.metrics) > 1:
            source_tables = {metric.table for metric in plan.metrics}
            safe_shape = (not plan.clarification and len(source_tables) == 1
                and not plan.derived_metrics and plan.analysis_mode == "aggregate"
                and plan.comparison_mode == "none" and top_n_safe
                and not plan.dimension_transforms and len(plan.dimensions) <= 1
                and bool(plan.coverage) and not plan.coverage.get("unresolved")
                and not plan.join_tables and not plan.join_conditions and not plan.join_path
                and not plan.fan_out and having_safe)
            if safe_shape and tables is not None:
                try:
                    MetricCompiler(self.planner).compile(plan, tables)
                except MetricPlanError:
                    pass
                else:
                    return True
        if os.getenv('ICT8_FAST_SQL', '0') != '1':
            return False
        from .complex_query import needs_complex_query
        # Listing verbs also occur in ordinary compiled Top-N requests.
        # Ignore only those verbs, and only with complete typed coverage;
        # NULL, ties, multi-stage aggregation and physical joins stay complex.
        complexity_question = re.sub(r'列出|列举|找出|分别统计', '', question)
        return (not needs_complex_query(complexity_question) and not plan.clarification
            and bool(plan.metrics or plan.metric_column) and bool(plan.coverage)
            and not plan.coverage.get('unresolved') and not plan.coverage.get('ignored_instruction_spans')
            and not plan.join_tables and not plan.join_conditions and not plan.join_path
            and not plan.fan_out and having_safe
            and (plan.comparison_mode == 'none' or plan.comparison_mode in {'同比','环比'}
                 and bool(plan.comparison_period) and not plan.metrics and not plan.derived_metrics)
            and plan.analysis_mode in {'aggregate','rank','trend'}
            and len({metric.table for metric in plan.metrics} or {plan.metric_table or plan.table}) == 1)

    def _has_fast_sql_rule_plan(self, question):
        """Check a fully covered rule plan against one current schema snapshot."""
        with self._connect() as connection:
            tables, index, revision = self._snapshot_for(connection)
            plan = self._rules_plan(question, tables, connection, index, cache_namespace=revision)
            return self._fast_sql_plan_eligible(plan, question, tables)

    @staticmethod
    def _single_metric_comparison_supported(plan):
        """Use the legacy comparison builder only for its fully represented shape."""
        if plan.comparison_mode not in {"同比", "环比"} or not plan.comparison_period:
            return False
        if len(plan.metrics) != 1 or plan.derived_metrics or plan.having or plan.top_n:
            return False
        metric = plan.metrics[0]
        if (plan.metric_table or plan.table) != metric.table:
            return False
        if (plan.metric_column, plan.metric_function) != (metric.column, metric.function):
            return False
        if metric.filters or plan.analysis_mode != "aggregate":
            return False
        if plan.order_metric not in {None, metric.id}:
            return False
        return not plan.output_metrics or plan.output_metrics == [metric.id]

    def _model_plan(self, question: str, tables, connection, index, cache_namespace=None,
                    source_required=None) -> QueryPlan:
        attempts, repair_attempted = [], False
        try:
            rule_plan = self._rules_plan(question, tables, connection, index, cache_namespace=cache_namespace)
            from ..fusion_constraints import verify_required_intent
            provider = self.model_plan_provider
            fast_path_enabled = (
                getattr(provider, 'supports_verified_rule_fast_path', False) is True
                or callable(getattr(provider, 'propose', None))
                and os.getenv('ICT8_FAST_SQL', '0') == '1'
            )
            if (fast_path_enabled and self._fast_sql_plan_eligible(rule_plan, question, tables)
                    and (source_required is None or not verify_required_intent(rule_plan,source_required))):
                rule_plan.planner_source = 'server_verified_fast_rules'
                rule_plan.planner_audit = {'candidate_source':'independent_rule_parser',
                    'final_source':'server_verified_fast_rules','decision':'accepted',
                    'fallback':False,'model_called':False,
                    'verification':'complete_coverage_single_table_current_snapshot'}
                return rule_plan
            propose = getattr(self.model_plan_provider, "propose", None)
            model_context = self._verified_model_intent(source_required or rule_plan)
            if source_required is not None:
                model_context['source'] = 'independent_original_source_scope_in_execution_snapshot'
                model_context['scope_question_sha256'] = hashlib.sha256(source_required.source_scope_question.encode()).hexdigest()
            payload = (propose(question, tables, model_context)
                       if callable(propose) else self.model_plan_provider(question, tables))
            for attempt in range(2):
                record = {'attempt': attempt+1, 'original_structure': diagnose(payload, tables)}
                attempts.append(record)
                if getattr(self.model_plan_provider, 'audit', None):
                    record['provider'] = dict(self.model_plan_provider.audit)
                try:
                    checked_payload, normalizations = canonicalize(payload)
                    record['structural_normalizations'] = normalizations
                    plan = self.model_plan_validator.validate(checked_payload, tables, question=question)
                    if plan.comparison_mode != "none":
                        normalized_period, period_format = self.planner.normalize_comparison_period(
                            plan.comparison_period, tables, connection, plan,
                            date_storage_cache=self._date_storage_cache,
                            date_storage_cache_namespace=cache_namespace,
                        )
                        if normalized_period is None:
                            raise ModelPlanError("模型计划的比较日期字段存储格式无法安全判断")
                        plan.comparison_period = normalized_period
                        plan.assumptions.append(f"日期参数格式：{period_format}")
                    self._check_model_grounding(plan, question, tables, index, connection,
                                                cache_namespace=cache_namespace, rule_plan=rule_plan)
                    _, bindings, invalid = association_scope(question, tables)
                    if invalid or any(not binding_present(plan, b) for b in bindings):
                        raise ModelPlanError('模型未保留原问题明确且可验证的关联关系')
                    label_changes = self._normalize_model_labels(plan, rule_plan)
                    if plan.metrics:
                        try:
                            if plan.comparison_mode == "none":
                                MetricCompiler(self.planner).compile(plan, tables)
                            elif self._single_metric_comparison_supported(plan):
                                self.planner.build_sql(plan)
                            else:
                                raise MetricPlanError("多指标周期比较尚需明确每个指标的时间角色",
                                                      "unsupported_comparison_combination")
                        except MetricPlanError as exc:
                            raise ModelPlanError(str(exc)) from exc
                    record['status'] = 'accepted'
                    break
                except ModelPlanError as exc:
                    record.update(status='rejected', reason_code=self._model_rejection_code(exc),
                                  reason=self._safe_model_rejection_reason(exc))
                    repair = getattr(self.model_plan_provider, 'repair', None)
                    blocked = rule_plan.clarification_code in {
                        'ambiguous_metric', 'ambiguous_dimension', 'unverified_join_condition',
                        'unsupported_exact_rank', 'conflicting_rank_selection', 'unverified_average_scope'}
                    structural = any(marker in str(exc) for marker in (
                        '指标 ID 重复', '指标 ID 或展示列名重复', '指标 ID 无效',
                        'metric.id', 'derived.id', 'output_metric', 'order_metric',
                        '展示名称无效', '输出指标不存在或重复', '排序指标不存在'))
                    # A repair fixes handles/output structure, not an unsafe
                    # field, missing filter, changed function or source scope.
                    if attempt or blocked or not structural or not callable(repair):
                        raise
                    repair_attempted = True
                    payload = repair(question, tables, model_context,
                        {'reason_code': record['reason_code'], 'original_structure': record['original_structure'],
                         'constraint': 'Return one corrected full plan; original source/metric/filter/time/rank constraints remain mandatory.'})
            plan.planner_audit = {
                "candidate_source": "external_model",
                "final_source": "model_validated",
                "fallback": False,
                "decision": "accepted",
            }
            plan.model_plan_diagnostics = {'attempts': attempts, 'repair_attempted': repair_attempted}
            if getattr(self.model_plan_provider, "audit", None):
                plan.planner_audit["provider"] = dict(self.model_plan_provider.audit)
            if label_changes:
                plan.planner_audit["label_normalizations"] = label_changes
            return plan
        except Exception as exc:  # 任意 provider/校验异常都只影响"提议"，不影响可用性
            if not self.model_fallback:
                raise
            plan = self._rules_plan(question, tables, connection, index, cache_namespace=cache_namespace)
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
            plan.model_plan_diagnostics = {'attempts': attempts, 'repair_attempted': repair_attempted}
            plan.assumptions.append(f"模型计划不可用或未通过校验（{rejection_reason}），已回退规则规划")
            return plan

    @staticmethod
    def _verified_model_intent(plan: QueryPlan) -> dict[str, Any]:
        """Expose grounded slots, never a generated SQL or executable rule plan."""
        metrics = [asdict(metric) for metric in plan.metrics]
        if not metrics and plan.metric_column and plan.metric_function:
            metrics = [{"table": plan.metric_table or plan.table, "column": plan.metric_column,
                        "function": plan.metric_function, "label": plan.metric_label}]
        return {"source": "independent_explicit_slot_extraction", "metrics": metrics,
                "filters": [item.to_dict() for item in plan.filters],
                "dimensions": [{"table": plan.dimension_tables.get(column, plan.table),
                    "column": column, "transform": plan.dimension_transforms.get(column, "raw"),
                    "label": plan.dimension_labels.get(column, column)} for column in plan.dimensions],
                "analysis_mode": plan.analysis_mode, "top_n": plan.top_n,
                "order_desc": plan.order_desc, "having": asdict(plan.having) if plan.having else None,
                "comparison_mode": plan.comparison_mode, "comparison_period": dict(plan.comparison_period),
                "clarification_code": plan.clarification_code,
                "unresolved": list(plan.coverage.get("unresolved", []))}

    @staticmethod
    def _normalize_model_labels(plan: QueryPlan, rule_plan: QueryPlan) -> list[dict[str, Any]]:
        """Canonicalize presentation only after independent semantic validation.

        Matching physical source and aggregation prevents a display alias from
        disguising a different metric. Ambiguous business labels remain intact.
        """
        candidates: dict[tuple, set[str]] = {}
        for metric in rule_plan.metrics:
            candidates.setdefault((metric.table, metric.column, metric.function), set()).add(metric.label)
        if not rule_plan.metrics and rule_plan.metric_label:
            candidates.setdefault((rule_plan.metric_table or rule_plan.table,
                                   rule_plan.metric_column, rule_plan.metric_function), set()).add(rule_plan.metric_label)
        changes = []
        for metric in plan.metrics:
            labels = candidates.get((metric.table, metric.column, metric.function), set())
            if len(labels) == 1:
                label = next(iter(labels))
                if metric.label != label:
                    changes.append({"metric_id": metric.id, "from": metric.label, "to": label,
                                    "reason": "verified_business_metric_label"})
                    metric.label = label
        labels = candidates.get((plan.metric_table or plan.table, plan.metric_column, plan.metric_function), set())
        if len(labels) == 1:
            label = next(iter(labels))
            if plan.metric_label != label:
                if not plan.metrics:
                    changes.append({"metric_id": None, "from": plan.metric_label, "to": label,
                                    "reason": "verified_business_metric_label"})
                plan.metric_label = label
        return changes

    @staticmethod
    def _safe_model_rejection_reason(exc: Exception) -> str:
        """限制审计文案，避免异常文本把控制字符或超长内容带入 API。"""

        if not isinstance(exc, ModelPlanError):
            return "provider_unavailable_or_invalid_response"
        text = "".join(ch for ch in str(exc) if ord(ch) >= 32 or ch in "\t\n")
        text = re.sub(r'“[^”]*”', '“<redacted>”', text)
        text = re.sub(r'sk-[A-Za-z0-9_-]{8,}|Bearer\s+[^\s,;]+', '<redacted>', text, flags=re.I)
        return text[:200]

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
            ("average_scope_unverified", "未核验的平均比较范围"),
            ("extra_dimension", "增加了问题中没有依据的分组维度"),
            ("clarification_bypass", "绕过了明确的分组维度澄清"),
            ("rank_selection_clarification_bypass", "绕过明确的名次选择澄清"),
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

    def _check_model_grounding(self, plan: QueryPlan, question: str, tables, index: ValueIndex, connection=None, *, cache_namespace=None, rule_plan=None) -> None:
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
        if rule_plan is None:
            rule_plan = self._rules_plan(question, tables, connection, index, cache_namespace=cache_namespace)
        if rule_plan.clarification_code in {'ambiguous_metric', 'ambiguous_dimension', 'ambiguous_value'}:
            # A physical name appearing in multiple sources does not ground
            # ownership. A high model confidence cannot replace the user's
            # missing source/metric choice, including newly quoted schemas.
            # Explicit owners and field selections are independently linked
            # first and therefore do not enter this ambiguity branch.
            raise ModelPlanError('模型计划不得绕过明确的同名字段或指标口径澄清')
        if rule_plan.clarification_code in {
            "unsupported_exact_rank", "conflicting_rank_selection",
        }:
            raise ModelPlanError("模型计划不得绕过明确的名次选择澄清")
        if rule_plan.clarification_code == 'unverified_average_scope':
            raise ModelPlanError('模型计划不得绕过未核验的平均比较范围')
        if rule_plan.metrics:
            expected = {(m.table, m.column, m.function) for m in rule_plan.metrics}
            actual = ({(m.table, m.column, m.function) for m in plan.metrics}
                      if plan.metrics else
                      ({(plan.metric_table or plan.table, plan.metric_column, plan.metric_function)}
                       if plan.metric_column and plan.metric_function else set()))
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
        question_grains = group_grains(normalized, tables)
        if 'ambiguous' in question_grains.values():
            raise ModelPlanError('模型计划不得绕过明确的时间分组粒度澄清')
        required_dimensions = {(rule_plan.dimension_tables.get(column, rule_plan.table), column)
                               for column in rule_plan.dimensions}
        actual_dimensions = {(plan.dimension_tables.get(column, plan.table), column)
                             for column in plan.dimensions}
        if not required_dimensions.issubset(actual_dimensions):
            raise ModelPlanError("模型计划遗漏了问题中的明确分组维度")
        extras = actual_dimensions - required_dimensions
        if extras and not self._grounded_dimensions(plan, extras, question, tables):
            # 模型不能把未出现在用户问题中的维度凭空加入结果，避免把总体
            # 聚合静默改变成分组聚合。只有规则层也能从问题中证明的维度才可执行。
            raise ModelPlanError("模型计划增加了问题中没有依据的分组维度")
        required_transforms = {
            (rule_plan.dimension_tables.get(column, rule_plan.table), column): question_grains.get(
                (rule_plan.dimension_tables.get(column, rule_plan.table), column), rule_plan.dimension_transforms.get(column, "raw"))
            for column in rule_plan.dimensions
        }
        actual_transforms = {
            (plan.dimension_tables.get(column, plan.table), column): plan.dimension_transforms.get(column, "raw")
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
        scopes = re.findall(r'按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总)', normalized)
        scopes += re.findall(r'(?:各|每个|每一)([^,;。?!？；]{1,160}?)(?:的|分组|统计|计算|汇总|排名|排行|占比|$)', normalized)
        scopes += [normalize_text(scope) for scope in re.findall(
            r'\b(?:group\s+by|by|per)\s+([^,;。?!？；]{1,160})', question.lower())]
        scopes = [scope for scope in scopes if not re.search(r'筛选|过滤|限定|等于|不等于|>=|<=|(?<![<>!])=', scope)]
        if not scopes:
            return False
        physical = group_fields(normalized, tables)
        grains = group_grains(normalized, tables)
        requested = {(link.table, link.column) for scope in scopes
                     for link in self.planner.linker.link('按' + scope + '分组', tables)
                     if link.role == 'dimension'}
        for table, column in dimensions:
            if (table, column) not in requested or (physical and (table, column) not in physical):
                return False
            if plan.dimension_transforms.get(column, 'raw') != grains.get((table, column), 'raw'):
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
    def extract_required_intent(self, scope_question: str) -> QueryPlan:
        """Extract a server-owned source clause without a model call or execution.

        Dependency runs can borrow the same pinned read snapshot. The returned
        plan is a constraint carrier, never an alternative executable plan.
        Ambiguity stays explicit rather than accepting guessed requirements.
        """
        if not isinstance(scope_question, str) or not scope_question.strip() or len(scope_question) > 1000:
            raise ValueError("源子句为空或超过长度上限")
        with self._connect() as connection:
            tables, index, revision = self._snapshot_for(connection)
            plan = self._rules_plan(scope_question, tables, connection, index, cache_namespace=revision)
            # This internal-only attribute is not part of model JSON or the
            # public QueryPlan serialization. Keep the original source clause,
            # not its rewritten/metric-expanded display string.
            plan.source_scope_question = scope_question
            return plan

    def answer(self, question: str, *, max_rows: int | None = None,
               required_intent: QueryPlan | None = None, complete_results: bool = False) -> QueryResult:
        # One read snapshot covers lexical binding, rule extraction, model
        # grounding and execution. The display always retains the user's text.
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 不能为空")
        sanitized_question, ignored_instructions = _strip_unsafe_instruction_noise(question)
        if ignored_instructions and not sanitized_question.strip():
            message = "问数只支持只读查询，无法执行删除或修改操作。请改成查询问题。"
            plan = QueryPlan(rewritten_question=question, clarification=message,
                clarification_code="read_only_query_required", planner_source="read_only_guard",
                coverage={"consumed": [], "unresolved": [],
                          "ignored_instruction_spans": ignored_instructions},
                planner_audit={"decision": "rejected", "model_called": False,
                               "reason": "write_intent_without_read_query"})
            return QueryResult(status="clarification", question=question,
                rewritten_question=question, sql=None, parameters=(), columns=(), rows=(),
                plan=plan.to_dict(), explanation=(message,), clarification=message,
                clarification_code=plan.clarification_code,
                provenance={"source_type": "structured_database", "execution_status": "not_executed"},
                result_state="unexecuted")
        with self.consistent_reads():
            semantic = self.normalize_question(question)
            if semantic is not None and semantic.ambiguities:
                plan = QueryPlan(rewritten_question=question,
                    clarification=semantic.clarification,
                    clarification_code='ambiguous_business_expression',
                    semantic_audit={'language_normalization':semantic.to_dict()})
                return QueryResult(status='clarification', question=question,
                    rewritten_question=question, sql=None, parameters=(), columns=(), rows=(),
                    plan=plan.to_dict(), explanation=(semantic.clarification,),
                    provenance={'source_type':'structured_database','execution_status':'not_executed'},
                    clarification=semantic.clarification, clarification_code=plan.clarification_code,
                    result_state='unexecuted')
            canonical = semantic.normalized_question if semantic is not None else question
            result = self._answer_canonical(canonical, max_rows=max_rows,
                required_intent=required_intent, complete_results=complete_results)
            if semantic is not None and semantic.changed:
                plan = dict(result.plan)
                plan['semantic_audit'] = {**plan.get('semantic_audit', {}),
                    'language_normalization':semantic.to_dict()}
                result = replace(result, question=question, plan=plan,
                    provenance={**result.provenance,'language_normalization':semantic.to_dict()})
            return result

    def _answer_canonical(self, question: str, *, max_rows: int | None = None,
               required_intent: QueryPlan | None = None, complete_results: bool = False) -> QueryResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 不能为空")
        row_cap = int(max_rows or self.max_rows)
        if type(complete_results) is not bool:
            raise ValueError('complete_results 必须为布尔值')
        # The original user's explicit all-rows request is itself opt-in. Keep
        # the preview cap and every existing complete-artifact resource budget.
        complete_results = complete_results or requests_complete_result(question)
        if complete_results:
            row_cap = min(100, row_cap, self.max_rows)
            if row_cap < 1:
                raise ValueError('preview 行数必须为正数')
        with self._connect() as connection:
            tables, index, revision = self._snapshot_for(connection)
            with _safety_stage('artifact_source_validation'):
                source_pin = (pin_database(self.database_path, self.complete_result_budgets,
                    generation=self._source_revision, expected_generation=revision[0]) if complete_results else None)
            source_constraint_audit = None
            current_required, errors = None, []
            if required_intent is not None:
                if not isinstance(required_intent, QueryPlan):
                    raise ValueError("源约束必须由服务器规则提取器生成")
                from ..fusion_constraints import (SourceConstraintError, server_project_required_intent,
                                                  verify_required_intent)
                # Re-extract in the actual execution snapshot. A previously
                # extracted scope cannot authorize stale values/date storage
                # after a concurrent source/schema change.
                scope_question = getattr(required_intent, "source_scope_question", None)
                if not isinstance(scope_question, str) or not scope_question.strip() or len(scope_question) > 1000:
                    raise ValueError("源约束缺少服务器保存的原始来源子句")
                subset = getattr(required_intent, "source_metric_subset", None)
                try:
                    try:
                        current_required = self._rules_plan(scope_question, tables, connection, index,
                                                           cache_namespace=revision)
                    except (KeyError, StopIteration):
                        raise SourceConstraintError('source_scope_unverified')
                    if subset is not None:
                        current_required = server_project_required_intent(current_required, subset)
                    dynamic_filters = getattr(required_intent, "source_dynamic_filters", None)
                    if dynamic_filters is not None:
                        from ..dynamic_source_binding import apply_dynamic_source_filters
                        if not isinstance(dynamic_filters, list) or len(dynamic_filters) != 1:
                            raise SourceConstraintError("source_dynamic_binding_unverified")
                        descriptor = dynamic_filters[0]
                        label = descriptor.get("dimension_label") if isinstance(descriptor, dict) else None
                        if not isinstance(label, str) or not label or len(label) > 100:
                            raise SourceConstraintError("source_dynamic_binding_unverified")
                        if any(not isinstance(descriptor.get(key), str) for key in ("table", "column")):
                            raise SourceConstraintError("source_dynamic_binding_unverified")
                        dimensions = {(link.table, link.column)
                                      for link in self.planner.linker.link(label, tables)
                                      if link.role == "dimension"}
                        if dimensions != {(descriptor.get("table"), descriptor.get("column"))}:
                            raise SourceConstraintError("source_dynamic_binding_unverified")
                        current_required = apply_dynamic_source_filters(
                            current_required, dynamic_filters, tables=tables,
                            scope_question=scope_question,
                        )
                    if current_required.clarification or current_required.coverage.get('unresolved'):
                        raise SourceConstraintError('source_scope_unverified')
                    current_required.source_scope_question = scope_question
                except SourceConstraintError as exc:
                    errors = [exc.code]
            compiled = None
            from .complex_query import needs_complex_query, propose_complex
            if errors:
                plan = QueryPlan(rewritten_question=question)
            elif self.model_plan_provider is None:
                plan = self._rules_plan(question, tables, connection, index, cache_namespace=revision)
            elif (required_intent is None and needs_complex_query(question)
                  and not self._fast_sql_plan_eligible(
                      self._rules_plan(question, tables, connection, index, cache_namespace=revision), question, tables)
                  and getattr(self.model_plan_provider, 'supports_complex_queries', False) is True):
                # A separate capability, not a bypass of typed fusion contracts.
                try:
                    from .date_profile import storage_profiles
                    probe_started = time.monotonic()
                    initial_profiles = storage_profiles(connection, tables, question)
                    def load_candidate_profiles(fields, existing):
                        observed = {item['field'] for item in existing}
                        missing = [(t, c) for t, c in fields if t + '.' + c not in observed]
                        # Proposal latency does not consume the snapshot probe
                        # budget: bound actual scan time across both stages.
                        additional = storage_profiles(connection, tables, '', candidate_fields=missing,
                            max_columns=max(0, 8 - len(existing)),
                            max_seconds=max(0.0, 2.0 - initial_probe_seconds))
                        return [*existing, *additional]
                    initial_probe_seconds = time.monotonic() - probe_started
                    plan, compiled = propose_complex(self.model_plan_provider, question, tables,
                        date_profiles=initial_profiles, date_profile_loader=load_candidate_profiles)
                except Exception as exc:
                    from ..responses_client import GenerationError
                    if isinstance(exc, GenerationError) and exc.status in (401, 403):
                        raise
                    plan = QueryPlan(rewritten_question=question, planner_source='complex_model_reviewed',
                        clarification='复杂查询未通过结构或模型核验，请明确查询字段、关联及统计口径。',
                        clarification_code='complex_query_validation_failed')
                    plan.planner_audit = {'decision':'rejected', 'error_type':type(exc).__name__,
                        'reason':str(exc) if isinstance(exc, SqlSafetyError) else 'model_proposal_failed'}
                    if isinstance(exc, SqlSafetyError):
                        plan.planner_audit['safety_diagnostics'] = _safe_failure_diagnostics(
                            exc, 'structural_validation')
            else:
                plan = self._model_plan(question, tables, connection, index, cache_namespace=revision,
                                        source_required=current_required)
            if required_intent is not None:
                if not errors:
                    errors = verify_required_intent(plan, current_required)
                source_constraint_audit = {
                    "status": "verified" if not errors else "rejected",
                    "error_codes": list(errors),
                    "scope_question_sha256": hashlib.sha256(scope_question.encode()).hexdigest(),
                    "verification": "typed_original_source_clause_before_sql_execution",
                }
                if errors:
                    plan.intent_audit = self._intent_audit(plan, question)
                    plan.intent_audit["source_constraint_validation"] = source_constraint_audit
                    message = "跨源SQL计划未保留原问题的来源、指标或过滤约束，请明确来源子句后重新规划。"
                    return QueryResult(
                        status="incomplete", question=question, rewritten_question=plan.rewritten_question,
                        sql=None, parameters=(), columns=(), rows=(), plan=plan.to_dict(),
                        explanation=(message,), clarification=message,
                        clarification_code="source_constraint_mismatch", result_state="unexecuted",
                        provenance={"source_type": "structured_database", "database": self.database_path.name,
                                    "consistency": "sqlite_read_transaction",
                                    "source_constraint_validation": source_constraint_audit},
                    )
            rank_return_audit = None
            if (not plan.clarification and plan.planner_source == 'model_validated'
                    and plan.analysis_mode == 'rank' and plan.top_n is not None
                    and plan.limit == plan.top_n):
                from ..fusion_constraints import verify_required_intent
                rank_scope = scope_question if required_intent is not None else question
                rank_required = (current_required if required_intent is not None else
                    self._rules_plan(rank_scope, tables, connection, index, cache_namespace=revision))
                # Top-N counts dense ranks, not result rows. Correct only an
                # independently proven rank scope in this same read snapshot.
                # Explicit row limits are user intent and are never enlarged.
                explicit_rows = re.search(
                    r'(?:返回|展示|显示|只看|仅看|只取|仅取|取|最多|仅|只要)\s*'
                    r'(?:(?:最多|仅|只要|前)\s*)*[0-9零一二三四五六七八九十百两]+\s*(?:条|行)'
                    r'|\b(?:return|show)\s+(?:(?:only|at\s+most)\s+)*\d+\s+(?:rows?|records?)\b'
                    r'|\blimit\s+\d+\b', rank_scope, re.I)
                # The current Chinese coverage lexer can ignore English
                # qualifiers. Unknown row-limit language is therefore a
                # veto, never affirmative evidence of an implicit cap.
                unknown_return_qualifier = re.search(
                    r'\b(?:return|show|limit|rows?|records?|only|at\s+most)\b'
                    r'|最多|仅取|只取|只要|限制(?:返回|条数|行数)', rank_scope, re.I)
                safe_cap = min(row_cap, self.max_rows, rank_required.limit)
                if (not explicit_rows and not unknown_return_qualifier
                        and not verify_required_intent(plan, rank_required)
                        and safe_cap > plan.limit):
                    old_limit = plan.limit
                    plan.limit = safe_cap
                    rank_return_audit = {'status': 'verified', 'from': old_limit, 'to': safe_cap,
                        'top_n': plan.top_n, 'reason': 'dense_rank_selection_separate_from_safe_row_cap',
                        'scope_question_sha256': hashlib.sha256(rank_scope.encode()).hexdigest(),
                        'verification': 'independent_original_scope_in_execution_snapshot'}
                    plan.planner_audit['rank_return_cap_normalization'] = rank_return_audit
            complete_scope = None
            if compiled is not None and required_intent is None and not plan.clarification:
                # Relational SQL has no implicit LIMIT. Deliver all bounded rows
                # through the existing artifact channel, with a capped preview.
                # This never enlarges execution budgets or treats preview as all.
                if not complete_results:
                    with _safety_stage('artifact_source_validation'):
                        source_pin = pin_database(self.database_path, self.complete_result_budgets,
                            generation=self._source_revision, expected_generation=revision[0])
                complete_results = True
                row_cap = min(100, row_cap, self.max_rows)
            if complete_results and not plan.clarification:
                with _safety_stage('result_scope_validation'):
                    complete_scope = configure_complete_scope(plan,
                        scope_question if required_intent is not None else question)
                if plan.preview_row_limit is not None:
                    row_cap = min(row_cap, plan.preview_row_limit)
                complete_scope['effective_preview_limit'] = row_cap
            if (compiled is None and not plan.clarification and (plan.metrics or plan.fan_out)
                    and not self._single_metric_comparison_supported(plan)):
                try:
                    compiled = self.metric_compiler.compile(plan, tables)
                except MetricPlanError as exc:
                    plan.clarification, plan.clarification_code = str(exc), exc.code
                    plan.confidence = 0.3
            plan.intent_audit = self._intent_audit(plan, question)
            if source_constraint_audit is not None:
                plan.intent_audit["source_constraint_validation"] = source_constraint_audit
            if rank_return_audit is not None:
                plan.intent_audit['rank_return_cap_normalization'] = rank_return_audit
            if plan.clarification:
                return QueryResult(
                    status="clarification", question=question, rewritten_question=plan.rewritten_question,
                    sql=None, parameters=(), columns=(), rows=(), plan=plan.to_dict(),
                    explanation=(plan.clarification,),
                    provenance={"source_type": "structured_database", "database": self.database_path.name,
                                "consistency": "sqlite_read_transaction"},
                    clarification=plan.clarification, clarification_code=plan.clarification_code,
                    clarification_options=tuple(plan.clarification_options),
                )
            sql, parameters = compiled or self.planner.build_sql(plan)
            if plan.model_plan_diagnostics.get('channel') == 'relational_sql':
                from .result_scope import enforce_relational_result_scope
                with _safety_stage('result_scope_validation'):
                    sql, parameters = enforce_relational_result_scope(plan, sql, parameters)
            complete_metadata = None
            if complete_results:
                # This producer owns both cursor execution and publication.
                # Without a specific allowlisted code, do not claim which of
                # those internal phases failed.
                with _safety_stage('complete_result_execution_and_delivery'):
                    execution = execute_complete_read_only(connection, sql, parameters,
                        database_path=self.database_path, artifact_dir=self.result_artifact_dir,
                        budgets=self.complete_result_budgets, preview_limit=row_cap,
                        expected_source=source_pin, generation=self._source_revision,
                        expected_generation=revision[0], scope=complete_scope)
                columns, rows = execution.columns, execution.preview_rows
                complete_metadata = {**execution.metadata, 'preview_cells': [list(row) for row in execution.preview_cells],
                    'preview_format': 'ordered_cell_arrays_preserve_duplicate_labels'}
                if complete_metadata['status'] == 'complete':
                    with self._lock:
                        self._result_bindings[complete_metadata['artifact_id']] = complete_metadata['binding_sha256']
                        while len(self._result_bindings) > 256:
                            self._result_bindings.popitem(last=False)
            else:
                with _safety_stage('execution'):
                    columns, rows = execute_read_only(connection, sql, parameters, max_rows=row_cap, max_steps=self.max_steps, max_seconds=self.max_seconds)
            result_state, notices = self._result_state(connection, plan, rows)
            effective_limit = min(plan.limit, row_cap)
            cardinality = scalar_aggregate_cardinality(sql) if complete_metadata is None else None
            limit_reached = len(rows) >= effective_limit if not complete_results else complete_metadata['preview_truncated']
            if cardinality is not None:
                # Execution has already succeeded. A single scalar aggregate
                # cannot hide a second output row behind the presentation cap.
                limit_reached = False
            if complete_metadata is not None:
                if complete_metadata['status'] == 'complete':
                    notices.append(f"预览 {len(rows)} 行；完整查询结果 {complete_metadata['row_count']} 行，已核验游标结束并保存可分页原始结果。")
                else:
                    notices.append('完整结果未通过资源预算；当前行仅为部分预览，不得作为完整答案。')
                    result_state = 'partial_rows'
            elif limit_reached:
                notices.append(f"结果达到返回上限{effective_limit}行，完整分组数量尚未核验；请细分过滤范围，不能将当前结果当作全部分组。")
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
            "row_limit": effective_limit,
            "result_completeness": (complete_metadata['status'] if complete_metadata is not None
                else "limit_reached_total_unknown" if limit_reached else "within_return_limit"),
            "query_hash": query_hash,
            "field_links": [item.to_dict() for item in plan.links],
            "coverage": plan.coverage,
            "consistency": "sqlite_read_transaction",
            "source_revision": hashlib.sha256(repr(revision).encode()).hexdigest(),
            "source_revision_kind": "file_generation_and_schema_version_not_content_hash",
            "grain_audit": plan.grain_audit,
            "result_cells": [
                {"row": i, "column": column, "query_hash": query_hash,
                 "locator": {"type": "sql_result", "row": i, "column": column}}
                for i, row in enumerate(rows) for column in row
            ],
        }
        if source_constraint_audit is not None:
            provenance["source_constraint_validation"] = source_constraint_audit
        if rank_return_audit is not None:
            provenance['rank_return_cap_normalization'] = rank_return_audit
        if complete_metadata is not None:
            provenance['complete_result'] = complete_metadata
        if cardinality is not None:
            provenance['output_cardinality'] = cardinality
        return QueryResult(
            status=('incomplete' if complete_metadata is not None and complete_metadata['status'] != 'complete' else 'ok'),
            question=question, rewritten_question=plan.rewritten_question, sql=sql,
            parameters=parameters, columns=columns, rows=rows, plan=plan.to_dict(),
            explanation=tuple(explanation), provenance=provenance, result_state=result_state, notices=tuple(notices),
        )

    def complete_result_page(self, artifact_id: str, *, offset: int = 0, page_size: int = 100,
                             expected_query_sha256: str | None = None,
                             expected_binding_sha256: str | None = None) -> dict:
        # Same-session calls may use the trusted producer record. After a
        # restart the client must supply the binding from its first response;
        # a metadata file cannot bootstrap its own authenticity.
        with self._lock:
            initial = self._result_bindings.get(artifact_id)
        if expected_binding_sha256 is None:
            expected_binding_sha256 = initial
        elif initial is not None and expected_binding_sha256 != initial:
            raise SqlSafetyError('result_artifact_initial_binding_mismatch')
        return read_result_page(self.result_artifact_dir, artifact_id, database_path=self.database_path,
                                offset=offset, page_size=page_size,
                                expected_query_sha256=expected_query_sha256,
                                expected_binding_sha256=expected_binding_sha256)

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
    def analyze_slots(self, question: str, *, preferred_tables=()) -> dict[str, Any]:
        with self._connect() as connection:
            tables, index, _revision = self._snapshot_for(connection)
            semantic = self._normalize_in_snapshot(question, tables, index, _revision)
        if semantic is not None and not semantic.ambiguities:
            question = semantic.normalized_question
        normalized = normalize_text(question)
        links = self.planner.linker.link(question, tables)
        # A declared derived metric is one linguistic slot backed by several
        # real source fields. Keep its original alias as the replacement span
        # instead of mistaking a substring (e.g. 成本) for a unit-cost column.
        if self.metric_catalog:
            _, expansion = self.metric_catalog.expand(question, tables)
            if expansion:
                derived_links, derived_aliases = [], []
                for metric_id in expansion['outputs']:
                    if metric_id not in self.metric_catalog.derived:
                        continue
                    aliases = sorted((alias for alias in self.metric_catalog.aliases[metric_id]
                                      if alias in normalized), key=len, reverse=True)
                    if not aliases:
                        continue
                    alias = aliases[0]
                    derived_aliases.append(alias)
                    sources, _ = self.metric_catalog.dependencies(metric_id)
                    for source in sources:
                        metric = self.metric_catalog.sources[source]
                        derived_links.append(LinkCandidate(alias, metric.table, metric.column,
                            'metric', 1.0, alias, metric.function))
                def inside_derived(link):
                    token = normalize_text(link.matched_alias)
                    positions = list(re.finditer(re.escape(token), normalized)) if token else []
                    spans = [m.span() for alias in derived_aliases for m in re.finditer(re.escape(alias), normalized)]
                    return positions and all(any(start <= m.start() and m.end() <= end
                        for start, end in spans) for m in positions)
                links = [link for link in links if link.role != 'metric' or not inside_derived(link)] + derived_links
        time_parse = lexicon.parse_time(normalized, self.reference_date)
        metric_links = [link for link in links if link.role == 'metric']
        best_scores = {}
        for link in metric_links:
            alias = normalize_text(link.matched_alias)
            best_scores[alias] = max(best_scores.get(alias, 0), link.score)
        metric_links = [link for link in metric_links
                        if link.score == best_scores[normalize_text(link.matched_alias)]]
        metric_links=list(dict.fromkeys(metric_links))
        metric_tables = {link.table for link in metric_links} or set(preferred_tables)
        matches, _ = index.match(normalized, {(link.table, link.column) for link in links},
                                 preferred_tables=metric_tables)
        return {
            "normalized": normalized,
            "metrics": metric_links,
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
        prev = self.analyze_slots(previous)
        cur = self.analyze_slots(current, preferred_tables={link.table for link in prev['metrics']})
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
