"""NL2SQL 的显式数据契约。

这些模型刻意不依赖模型供应商，便于后续把 LLM 规划器接入同一个安全执行门。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    primary_key: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForeignKeyInfo:
    table: str
    from_column: str
    to_column: str
    constraint_id: int | None = None
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[ColumnInfo, ...]
    row_count: int | None = None
    foreign_keys: tuple[ForeignKeyInfo, ...] = ()
    unique_keys: tuple[tuple[str, ...], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "columns": [column.to_dict() for column in self.columns],
        }
        if self.row_count is not None:
            result["row_count"] = self.row_count
        result["foreign_keys"] = [item.to_dict() for item in self.foreign_keys]
        result["unique_keys"] = [list(key) for key in self.unique_keys]
        return result


@dataclass(frozen=True)
class LinkCandidate:
    source_text: str
    table: str
    column: str
    role: str
    score: float
    matched_alias: str
    metric_function: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterSpec:
    column: str
    operator: str
    value: Any
    source_text: str
    explanation: str
    table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HavingSpec:
    operator: str
    mode: str
    value: Any
    source_text: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricSpec:
    """一个事实表上的聚合；grain 从实际主键读取，不能由模型宣称。"""

    id: str
    table: str
    column: str
    function: str
    label: str
    unit: str = "unknown"
    currency: str | None = None
    missing: str = "null"
    filters: list[FilterSpec] = field(default_factory=list)


@dataclass
class DerivedMetricSpec:
    id: str
    label: str
    expression: dict[str, Any]


@dataclass
class QueryPlan:
    table: str | None = None
    metric_table: str | None = None
    metric_column: str | None = None
    metric_function: str | None = None
    metric_label: str | None = None
    analysis_mode: str = "aggregate"
    dimensions: list[str] = field(default_factory=list)
    dimension_tables: dict[str, str] = field(default_factory=dict)
    dimension_transforms: dict[str, str] = field(default_factory=dict)
    dimension_labels: dict[str, str] = field(default_factory=dict)
    filters: list[FilterSpec] = field(default_factory=list)
    having: HavingSpec | None = None
    links: list[LinkCandidate] = field(default_factory=list)
    join_tables: list[str] = field(default_factory=list)
    join_conditions: list[str] = field(default_factory=list)
    join_path: list[dict[str, str]] = field(default_factory=list)
    join_alternatives: list[dict[str, str]] = field(default_factory=list)
    order_desc: bool = True
    limit: int = 100
    # Opt-in complete artifacts separate preview caps from user SQL semantics.
    complete_results: bool = False
    semantic_row_limit: int | None = None
    confidence: float = 0.0
    rewritten_question: str = ""
    comparison_mode: str = "none"
    comparison_period: dict[str, str] = field(default_factory=dict)
    clarification: str | None = None
    clarification_code: str | None = None
    clarification_options: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    planner_source: str = "rules"
    # Top-N：按 DENSE_RANK 取前 N（含并列），与安全上限 limit 分离
    top_n: int | None = None
    # JOIN 路径含一对多边时为 True；计数改为 COUNT(DISTINCT 主键) 防止扇出重复
    fan_out: bool = False
    # 覆盖率守卫的审计信息：哪些问题片段被哪个槽位消费
    coverage: dict[str, Any] = field(default_factory=dict)
    # 执行前意图审计：供 API/UI 展示，不参与 SQL 拼接
    intent_audit: dict[str, Any] = field(default_factory=dict)
    # 模型/规则规划来源审计：只记录安全的类别和最终决策，不保存模型原文。
    planner_audit: dict[str, Any] = field(default_factory=dict)
    # Bounded structural diagnostics, with values/labels/opaque IDs hashed.
    model_plan_diagnostics: dict[str, Any] = field(default_factory=dict)
    # v2: 每个事实表独立聚合，再按共享维度合并，禁止明细表互相放大。
    metrics: list[MetricSpec] = field(default_factory=list)
    derived_metrics: list[DerivedMetricSpec] = field(default_factory=list)
    order_metric: str | None = None
    grain_audit: dict[str, Any] = field(default_factory=dict)
    semantic_audit: dict[str, Any] = field(default_factory=dict)
    output_metrics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["filters"] = [item.to_dict() for item in self.filters]
        result["links"] = [item.to_dict() for item in self.links]
        result["having"] = self.having.to_dict() if self.having else None
        return result


@dataclass(frozen=True)
class QueryResult:
    status: str
    question: str
    rewritten_question: str
    sql: str | None
    parameters: tuple[Any, ...]
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    plan: dict[str, Any]
    explanation: tuple[str, ...]
    provenance: dict[str, Any]
    clarification: str | None = None
    clarification_code: str | None = None
    clarification_options: tuple[dict[str, str], ...] = ()
    # rows | empty：status=ok 时区分"有结果"与"该范围无数据"
    result_state: str = "rows"
    notices: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "result_state": self.result_state,
            "notices": list(self.notices),
            "question": self.question,
            "rewritten_question": self.rewritten_question,
            "sql": self.sql,
            "parameters": list(self.parameters),
            "columns": list(self.columns),
            "rows": [dict(row) for row in self.rows],
            "plan": self.plan,
            "explanation": list(self.explanation),
            "provenance": self.provenance,
            "clarification": self.clarification,
            "clarification_code": self.clarification_code,
            "clarification_options": [dict(item) for item in self.clarification_options],
        }
