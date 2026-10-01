"""赛题八可解释规则规划器（v2）。

相对 v1 的关键变化（每一项都对应一个已实测的静默错误，见 docs/NL2SQL_OPTIMIZATION.md）：

1. 实体值链接不依赖列别名（ValueIndex），支持多值 IN、否定 NOT IN、词头歧义澄清；
2. 时间统一由 lexicon.parse_time 归一化（季度/半年/区间/相对时间/ISO/中文年份）；
3. 阈值极性与单位（不超过/至少/万/亿）、Top-N 与"前 N 个月"分离；
4. "高于平均"改为与分组聚合值的平均比较，并复用同一 WHERE；
5. JOIN 路径标注基数，一对多扇出时计数改 COUNT(DISTINCT)，SUM/AVG 转澄清；
6. 时间过滤列绑定到指标主题（多对一可达的最近日期列），不再取"表名排序第一"；
7. "X所在Y"限定路径经过 X 所在表；
8. 覆盖率守卫：未被任何槽位消费的时间词、数字、否定词、并列实体触发澄清。

所有规则描述的是语言现象或 Schema 结构，没有任何题目级特判。
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, deque
from datetime import date, datetime, timezone
from typing import Any

from . import lexicon
from .models import FilterSpec, HavingSpec, QueryPlan, TableInfo
from .schema import SchemaLinker, normalize_text
from .value_index import ValueIndex
from .date_semantics import GENERIC_TIME_ALIASES, is_date_column

_DEFAULT_MAX_JOIN_HOPS = 4
_JOIN_HINT_RE = re.compile(r"\[join_path:([A-Za-z0-9_.>\-]+)\]")
_FIELD_HINT_RE = re.compile(r"\[field:(metric|dimension):([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\]")
_ANALYSIS_WORDS = ("排名", "排行", "名次", "占比", "份额", "比例")
_AGGREGATE_CUES = (
    (("去重计数",), "COUNT_DISTINCT", "去重数"),
    (("记录计数", "记录数", "计数"), "COUNT", "记录数"),
    (("平均", "均价", "均值", "均分", "平均值"), "AVG", "平均"),
    (("最高值", "最大值", "单笔最高", "单次最高"), "MAX", "最高"),
    (("最低值", "最小值", "单笔最低", "单次最低"), "MIN", "最低"),
    (("总计", "总和", "合计"), "SUM", "总"),
)
_AVERAGE_THRESHOLD_RE = re.compile(r"(?:高于|超过|大于|低于|小于|不低于|不高于)(?:整体|全部|总体)?(?:的)?平均")
_TREND_WORDS = ("趋势", "按月", "按年", "按日期", "每天", "每月", "每年", "按季度")
_GENERIC_TIME_ALIASES = GENERIC_TIME_ALIASES
_MAX_LIMIT = 100
_COVERAGE_STRUCTURAL_WORDS = (
    "订单", "工单", "明细", "金额", "总额", "总金额", "销售额", "收入", "销售",
    "数据", "查询", "统计", "各", "每个", "按", "分别", "的", "有", "是多少",
    "那边", "告诉我", "情况", "政策", "规定", "说明", "只看", "把", "列出来",
)
# 用户可能把破坏性指令和合法的统计意图写在同一句里。此处只移除带有明确
# 顺序词的自然语言指令片段；不会接受原始 SQL，也不会放宽执行层的只读限制。
_UNSAFE_INSTRUCTION_RE = re.compile(
    r"(?:"
    r"(?:请|帮我)?(?:忽略|无视|跳过|绕过|不要遵守).{0,24}?(?:规则|指令|限制)[，,;；]*"
    r"|(?:删除|清空|修改|更新|插入|新建|创建|drop|delete|update|insert|alter|truncate)"
    r"[^，,。；;]{0,40}?(?:之后|以后|后|然后|再|并且|并|，|,|；|;|。)"
    r")",
    re.IGNORECASE,
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _locate(text: str, span: str, occupied: list[tuple[int, int]]) -> tuple[int, int] | None:
    start = 0
    while span:
        index = text.find(span, start)
        if index < 0:
            return None
        end = index + len(span)
        if all(end <= a or index >= b for a, b in occupied):
            return index, end
        start = index + 1
    return None


def _strip_unsafe_instruction_noise(question: str) -> tuple[str, list[str]]:
    """保留句中可验证的只读查询意图，丢弃不能执行的自然语言操作指令。"""

    ignored: list[str] = []

    def replace(match: re.Match[str]) -> str:
        ignored.append(match.group(0))
        return " "

    return _UNSAFE_INSTRUCTION_RE.sub(replace, question), ignored


class SingleTablePlanner:
    def __init__(self, linker: SchemaLinker | None = None, *, max_join_hops: int = _DEFAULT_MAX_JOIN_HOPS):
        self.linker = linker or SchemaLinker()
        if max_join_hops < 1:
            raise ValueError("max_join_hops 必须大于 0")
        self.max_join_hops = max_join_hops

    # ================================================================ plan
    def plan(
        self,
        question: str,
        tables: tuple[TableInfo, ...],
        connection: sqlite3.Connection,
        *,
        value_index: ValueIndex | None = None,
        reference_date: date | None = None,
        date_storage_cache: dict[tuple[Any, ...], tuple[str, tuple[Any, Any, str]]] | None = None,
        date_storage_cache_namespace: tuple | None = None,
    ) -> QueryPlan:
        path_hint = self._path_hint(question)
        clean_question = _JOIN_HINT_RE.sub("", question or "")
        hints = _FIELD_HINT_RE.findall(clean_question)
        clean_question = _FIELD_HINT_RE.sub("", clean_question)
        clean_question, ignored_instructions = _strip_unsafe_instruction_noise(clean_question)
        normalized = normalize_text(clean_question)
        links = self.linker.link(clean_question, tables)
        for role, target_table, target_column in hints:
            chosen = [link for link in links if (link.role, link.table, link.column) == (role, target_table, target_column)]
            if not chosen or sum(1 for hint in hints if hint[0] == role) > 1:
                invalid = QueryPlan(rewritten_question=clean_question)
                return self._clarify(invalid, "invalid_field_selection", "选中的字段与原问题或当前 Schema 不匹配。", 0.0)
            aliases = {normalize_text(link.matched_alias) for link in chosen}
            links = [link for link in links if link.role != role or
                ((link.table, link.column) == (target_table, target_column)) or
                (role == "dimension" and normalize_text(link.matched_alias) not in aliases)]
        table = self._choose_table(normalized, tables, links)
        plan = QueryPlan(table=table.name if table else None, rewritten_question=clean_question.strip() or (question or "").strip())
        consumed: list[str] = []
        if table is None:
            return self._clarify(plan, "missing_data_subject", "当前数据源没有识别出可查询的数据表，请说明业务对象或数据主题。", 0.1)
        metric_alias_pairs: dict[str, set[tuple[str, str]]] = {}
        metric_aliases: dict[str, str] = {}
        for link in links:
            if link.role != "metric":
                continue
            alias = normalize_text(link.matched_alias)
            if alias:
                metric_alias_pairs.setdefault(alias, set()).add((link.table, link.column))
                metric_aliases[alias] = link.matched_alias
        best_metric_scores = {
            alias: max(link.score for link in links if link.role == "metric" and normalize_text(link.matched_alias) == alias)
            for alias in metric_alias_pairs
        }
        ambiguous_metrics = [
            (alias, pairs) for alias, pairs in metric_alias_pairs.items()
            if len({(link.table, link.column) for link in links
                    if link.role == "metric" and normalize_text(link.matched_alias) == alias
                    and link.score == best_metric_scores[alias]}) > 1
        ]
        if ambiguous_metrics:
            alias, pairs = sorted(ambiguous_metrics, key=lambda item: item[0])[0]
            options = [
                {"value": f"{target_table}.{column}", "label": f"{metric_aliases[alias]}（{target_table}.{column}）"}
                for target_table, column in sorted(pairs)
            ]
            return self._clarify(plan, "ambiguous_metric", f"“{metric_aliases[alias]}”对应多个指标字段，请明确统计口径。", 0.3, options)
        if ignored_instructions:
            plan.assumptions.append("检测到不可执行的操作或指令性片段，已忽略，仅处理剩余的只读统计意图。")
        plan.links = links
        consumed.extend(normalize_text(link.matched_alias) for link in links)
        for link in links:
            if normalize_text(link.matched_alias) != normalize_text(link.source_text) and link.score <= 0.55:
                plan.assumptions.append(f"“{link.matched_alias}”按近似词识别为“{link.source_text}”（可能是错别字）")
        if normalize_text(question or "") != re.sub(r"\s+", "", (question or "")).lower().replace("，", ","):
            plan.assumptions.append("问题中的繁体字已按简体归一化后解析")
        metric_links = [link for link in links if link.role == "metric" and link.table == table.name]
        all_metric_links = []
        for link in sorted((x for x in links if x.role == "metric"), key=lambda x: normalized.find(normalize_text(x.matched_alias))):
            if not any((x.table, x.column) == (link.table, link.column) for x in all_metric_links):
                all_metric_links.append(link)
        multiple_requested = len(all_metric_links) > 1 and any(
            re.search(r"(?:和|及|与|、|同时|以及|,)", normalized[
                normalized.find(normalize_text(left.matched_alias)) + len(normalize_text(left.matched_alias)):
                normalized.find(normalize_text(right.matched_alias))
            ]) for left, right in zip(all_metric_links, all_metric_links[1:])
        )
        if len({link.column for link in metric_links}) > 1 and not multiple_requested:
            seen: set[str] = set()
            options = []
            for link in metric_links:
                if link.column not in seen:
                    seen.add(link.column)
                    options.append({"value": link.column, "label": link.matched_alias})
            return self._clarify(plan, "ambiguous_metric", "问题同时包含多个指标，请选择需要统计的指标。", 0.3, options)
        metric = self._choose_metric(links, normalized, table)
        if metric is None:
            return self._clarify(
                plan, "missing_metric", "请补充要统计的指标，例如" + "、".join(o["label"] for o in self._metric_options(tables)[:3]) + "。",
                0.18, self._metric_options(tables),
            )
        plan.metric_table, plan.metric_column, plan.metric_function, plan.metric_label = metric
        if not multiple_requested:
            # Only consume aggregate words implemented by the selected slot.
            # A comparison against an average is a HAVING condition, not AVG.
            aggregate_text = _AVERAGE_THRESHOLD_RE.sub("", normalized)
            requested_functions = {function for _, function in self._aggregate_matches(aggregate_text)}
            if len(requested_functions) > 1:
                return self._clarify(plan, "ambiguous_aggregation",
                    "同一指标包含多个聚合口径，请分别查询或明确要使用的聚合函数。", 0.3)
            consumed.extend(cue for cue, function in self._aggregate_matches(aggregate_text)
                            if function == plan.metric_function)
        if multiple_requested:
            from .models import MetricSpec
            by_name = {item.name: item for item in tables}
            for i, link in enumerate(all_metric_links):
                clause = self._metric_clause(normalized, link)
                aggregate_text = _AVERAGE_THRESHOLD_RE.sub("", clause)
                functions = {fn for _, fn in self._aggregate_matches(aggregate_text)}
                if len(functions) > 1:
                    return self._clarify(plan, 'ambiguous_aggregation', '同一指标包含多个聚合口径，请分别查询或明确聚合函数。', 0.3)
                picked = self._choose_metric([link], clause, by_name[link.table])
                if picked:
                    mt, mc, fn, label = picked
                    consumed.extend(cue for cue, function in self._aggregate_matches(aggregate_text) if function == fn)
                    plan.metrics.append(MetricSpec(f"m{i}", mt, mc, fn, label,
                                                  "count" if fn in {"COUNT", "COUNT_DISTINCT"} else "unknown"))
            if len(plan.metrics) > 8:
                return self._clarify(plan, "ambiguous_metric", "一次最多支持 8 个指标，请缩小统计范围。", 0.3)
            first = plan.metrics[0]
            table = by_name[first.table]
            plan.table = first.table
            plan.metric_table, plan.metric_column = first.table, first.column
            plan.metric_function, plan.metric_label = first.function, first.label
        # COUNT currently compiles COUNT(*) rather than COUNT(column). For an
        # explicitly named nullable field these have different meanings; never
        # silently promise the non-null-field count. INTEGER single PK and
        # declared NOT NULL fields establish equivalence without reading data.
        for link in all_metric_links:
            clause = self._metric_clause(normalized, link)
            explicit_count = bool(re.search(r'(?:记录计数|记录数|计数)', self._count_cue_text(clause)))
            column_table = next(item for item in tables if item.name == link.table)
            column = next(item for item in column_table.columns if item.name == link.column)
            integer_pk = (column.primary_key and column.data_type.upper().strip() == 'INTEGER'
                          and sum(c.primary_key for c in column_table.columns) == 1)
            if explicit_count and column.nullable and not integer_pk:
                return self._clarify(plan, 'ambiguous_count_semantics',
                    '该字段可为空，请明确需要记录行数还是非空字段计数；当前不将COUNT(*)冒充COUNT(字段)。', 0.3)
            if plan.metric_function == 'COUNT_DISTINCT' and '去重' in clause:
                consumed.append('去重')
        reachable = self._many_to_one_distances(plan.metric_table, tables)

        # ---- "X所在Y"：X 只限定关联路径，不作为分组维度
        qualifier_table, qualifier_link = None, None
        for link in links:
            if link.role == "dimension" and (normalize_text(link.matched_alias) + "所在") in normalized:
                qualifier_table, qualifier_link = link.table, link
                consumed.append("所在")
                break
        dimension_links = [link for link in links if link is not qualifier_link]
        if any(word in normalized for word in ('按月','每月')) and any(word in normalized for word in ('按年','每年')):
            return self._clarify(plan,'ambiguous_time_grain','同时请求了按月和按年，请明确一种时间分组粒度。',0.3)
        (
            plan.dimensions,
            plan.dimension_tables,
            plan.dimension_transforms,
            plan.dimension_labels,
        ) = self._choose_dimensions(dimension_links, normalized, table, plan.metric_column, links, tables=tables)
        for grain, cues in (('month', ('按月', '每月')), ('year', ('按年', '每年'))):
            if grain in plan.dimension_transforms.values():
                consumed.extend(cue for cue in cues if cue in normalized)

        explicit_grouping = any(word in normalized for word in ("各", "每个", "按", "分别", "分组"))
        unsupported_grain = re.search(r"(按周|按天|按日|每天|每日|每周|按季度|每季度)", normalized)
        if unsupported_grain:
            return self._clarify(
                plan,
                "unsupported_time_grain",
                f"暂不支持“{unsupported_grain.group(1)}”的时间分组，请改用按月或按年，或明确日期字段和分组口径。",
                0.3,
            )
        if explicit_grouping and not plan.dimensions:
            # 明确要求分组却没有链接到任何维度时，禁止退化为总体聚合。
            # 这对冷启动 Schema 尤其重要：未知维度宁可澄清，也不能静默丢失。
            return self._clarify(
                plan,
                "missing_group_dimension",
                "问题要求按维度分组，但未识别出可用的分组字段，请明确字段或业务口径。",
                0.25,
                self._dimension_options(tables),
            )
        dimension_aliases: dict[str, set[tuple[str, str]]] = {}
        for link in dimension_links:
            if self._is_date_column(link.column, self._schema_column_type(tables, link.table, link.column)):
                continue
            dimension_aliases.setdefault(normalize_text(link.matched_alias), set()).add((link.table, link.column))
        ambiguous_dimensions = {
            alias: {
                (link.table, link.column) for link in dimension_links
                if normalize_text(link.matched_alias) == alias and link.score == max(
                    candidate.score for candidate in dimension_links
                    if normalize_text(candidate.matched_alias) == alias
                )
            }
            for alias, columns in dimension_aliases.items()
            if alias and len(columns) > 1 and alias in normalized
        }
        ambiguous_dimensions = {alias: columns for alias, columns in ambiguous_dimensions.items() if len(columns) > 1}
        if explicit_grouping and ambiguous_dimensions:
            alias, columns = sorted(ambiguous_dimensions.items())[0]
            options = [
                {"value": f"{target_table}.{column}", "label": f"{alias}（{target_table}.{column}）"}
                for target_table, column in sorted(columns)
            ]
            return self._clarify(plan, "ambiguous_dimension", f"“{alias}”对应多个可分组字段，请明确业务口径。", 0.3, options)

        plan.analysis_mode = self._analysis_mode(normalized)
        consumed.extend(word for word in _ANALYSIS_WORDS if word in normalized)
        # 当前 SQL 生成器只实现月/年日期变换；未实现的日期粒度必须进入
        # 覆盖率守卫并澄清，不能默默退化为总体聚合。
        ordinals = lexicon.parse_ordinal_ranks(normalized)
        if ordinals and (len(ordinals) != 1 or ordinals[0].n != 1):
            return self._clarify(
                plan, "unsupported_exact_rank",
                "第 N 名表示只取该名次，不能等同于前 N 名。当前支持第一名（含并列）；请明确是否要查询前 N 名，或只查询指定名次。", 0.3,
            )
        if ordinals:
            remaining_rank_text = normalized.replace(ordinals[0].span, "", 1)
            other_top = lexicon.parse_top_n(remaining_rank_text)
            if other_top is not None and (other_top.n != 1 or other_top.descending != ordinals[0].descending):
                return self._clarify(
                    plan, "conflicting_rank_selection",
                    "问题同时包含不一致的名次范围或排序方向，请选择一个明确的排名条件。", 0.3,
                )
        top = lexicon.parse_top_n(normalized)
        if top is None and plan.dimensions:
            # "某指标最高的某维度""冠军"：按聚合值取第 1 名（含并列），而不是把指标改成 MAX
            superlative = re.search(r"(最高|最多|最大|冠军|最低|最少|最小|垫底)", normalized)
            if superlative:
                top = lexicon.TopN(1, superlative.group(1) not in {"最低", "最少", "最小", "垫底"}, superlative.group(1))
        if top:
            plan.top_n = max(1, min(_MAX_LIMIT, top.n))
            plan.order_desc = top.descending
            consumed.append(top.span)
            if top.n > _MAX_LIMIT:
                plan.assumptions.append(f"Top-N 请求 {top.n} 超过安全上限，已限制为 {_MAX_LIMIT}")
        if (plan.analysis_mode in {"rank", "share"} or plan.top_n) and not plan.dimensions:
            return self._clarify(
                plan, "missing_analysis_dimension", "排名、占比或 Top-N 查询需要明确分组维度。", 0.35,
                self._dimension_options(tables),
            )

        # ---- 时间
        time_parse = lexicon.parse_time(normalized, reference_date)
        consumed.extend(time_parse.spans)
        plan.assumptions.extend(time_parse.assumptions)
        if time_parse.error_code:
            return self._clarify(plan, time_parse.error_code, time_parse.error_message or "时间范围无法确定。", 0.3)
        needs_date_role = (time_parse.single is not None or bool(plan.dimension_transforms)
                           or any(word in normalized for word in ('同比', '环比')))
        date_binding = self._bind_date_column(links, tables, plan.metric_table, reachable, plan) if needs_date_role else None
        self._rebind_date_dimensions(plan, tables, reachable, links)
        if plan.clarification:
            return plan

        comparison_words = tuple(word for word in ("同比", "环比") if word in normalized)
        consumed.extend(comparison_words)
        if len(comparison_words) > 1:
            return self._clarify(plan, "ambiguous_comparison_mode", "同比和环比不能在同一问题中混用，请选择一种比较口径。", 0.25)
        if comparison_words:
            if plan.analysis_mode in {"rank", "share"} or plan.top_n:
                return self._clarify(plan, "unsupported_comparison_combination", "同比/环比暂不与排名、占比或 Top-N 叠加，请先选择一种分析口径。", 0.25)
            if any(plan.dimension_transforms.get(column) in {"month", "year"} for column in plan.dimensions):
                return self._clarify(plan, "unsupported_comparison_grain", "同比/环比按月或按年逐期展开需要逐组对齐周期，当前请指定单个月份、季度或年份。", 0.3)
            period = self._comparison_period(comparison_words[0], time_parse.single, date_binding)
            if period is None:
                return self._clarify(
                    plan, "missing_comparison_period",
                    "同比需要明确年份、季度或月份，环比需要明确月份、季度或半年，例如在问题中写明“2025年”“2025年3月”或“2025年第一季度”。", 0.35,
                    ([{"value": "year", "label": "指定年份同比"}, {"value": "month", "label": "指定月份同比"}]
                     if comparison_words[0]=='同比' else [{"value": "month", "label": "指定月份环比"}]),
                )
            # 比较窗口也必须使用日期列的实际存储格式。
            period, period_format = self._comparison_parameters(
                period, tables, connection, plan,
                date_storage_cache=date_storage_cache,
                date_storage_cache_namespace=date_storage_cache_namespace,
            )
            if period is None:
                return self._clarify(plan, "ambiguous_date_storage", "日期字段的存储格式无法安全判断，请明确日期字段格式。", 0.25)
            plan.comparison_mode, plan.comparison_period = comparison_words[0], period
            plan.assumptions.append(f"比较窗口：{period['current_start_text']} 至 {period['current_end_text']}（半开区间）")
            plan.assumptions.append(f"日期参数格式：{period_format}")
        elif time_parse.single is not None:
            if date_binding is None:
                return self._clarify(plan, "missing_date_column", "指标所在主题没有可用的日期字段，无法按时间过滤，请说明时间口径。", 0.3)
            start, end = time_parse.single.iso()
            date_binding, date_params = self._date_parameters(
                date_binding, tables, connection, start, end, plan,
                cache=date_storage_cache, cache_namespace=date_storage_cache_namespace,
            )
            if date_binding is None:
                return self._clarify(plan, "ambiguous_date_storage", "日期字段的存储格式无法安全判断，请明确日期字段格式。", 0.25)
            plan.filters.append(FilterSpec(
                date_binding[1], "RANGE", date_params[:2], time_parse.single.span,
                f"限制时间 [{start}, {end})，时间字段 {date_binding[0]}.{date_binding[1]}", date_binding[0],
            ))
            plan.assumptions.append(f"日期参数格式：{date_params[2]}")

        # SQLite strftime does not interpret Unix epochs without an explicit
        # conversion. Until calendar grouping supports that conversion, refuse
        # it instead of collapsing all numeric timestamps into a NULL group.
        for column, transform in plan.dimension_transforms.items():
            if transform not in {'month', 'year'}:
                continue
            binding = (plan.dimension_tables.get(column, plan.table), column)
            actual, params = self._date_parameters(binding, tables, connection,
                '2000-01-01', '2000-01-02', plan,
                cache=date_storage_cache, cache_namespace=date_storage_cache_namespace)
            if actual is None:
                return self._clarify(plan, 'ambiguous_date_storage',
                    '时间分组字段的日期存储格式无法安全判断，请明确格式。', 0.25)
            if params[2] != 'iso_text':
                return self._clarify(plan, 'unsupported_time_storage',
                    '当前月/年分组仅支持ISO文本日期，数值时间戳的日历分组尚未实现。', 0.3)

        # ---- 实体值
        linked_columns = {(link.table, link.column) for link in links}
        if value_index is not None:
            blocked = [pos for pos in (_locate(normalized, span, []) for span in time_parse.spans) if pos]
            matches, ambiguities = value_index.match(normalized, linked_columns, blocked=blocked)
            if ambiguities:
                item = ambiguities[0]
                options = [
                    {"value": f"{item.span}=>{self._column_hint(entry.table, entry.column)}{entry.value}" if item.kind == "column" else f"{item.span}=>{entry.value}",
                     "label": f"{entry.value}（{entry.table}.{entry.column}）"}
                    for entry in item.candidates[:8]
                ]
                return self._clarify(plan, "ambiguous_value", f"“{item.span}”对应多个取值，请选择具体对象。", 0.3, options)
            value_filters, value_spans = self._filters_from_values(normalized, matches)
            plan.filters.extend(value_filters)
            consumed.extend(value_spans)
            for match in matches:
                if match.via != "exact":
                    plan.assumptions.append(f"“{match.span}”按{'同义词' if match.via == 'synonym' else '唯一前缀'}识别为 {match.column}={match.value}")
        else:
            legacy = self._value_filters(clean_question, links, tables, connection)
            plan.filters.extend(legacy)
            consumed.extend(normalize_text(item.source_text) for item in legacy)

        # 单值过滤的字段不自动成为分组："华南地区各渠道"只按渠道分组。
        # 用户明确写"按地区"/"各地区"时仍保留该维度。
        for dimension in list(plan.dimensions):
            target_table = plan.dimension_tables.get(dimension, table.name)
            fixed = any(f.table == target_table and f.column == dimension and
                        (f.operator == "=" or f.operator == "IN" and len(f.value) == 1) for f in plan.filters)
            aliases = [normalize_text(link.matched_alias) for link in links
                       if link.table == target_table and link.column == dimension and link.role == "dimension"]
            explicit_dimension = any(re.search(r"(?:按|各|每个|每|分别按)" + re.escape(alias), normalized) for alias in aliases)
            if fixed and not explicit_dimension:
                plan.dimensions.remove(dimension)
                plan.dimension_tables.pop(dimension, None)
                plan.dimension_labels.pop(dimension, None)
                plan.dimension_transforms.pop(dimension, None)

        # ---- 阈值
        threshold = lexicon.parse_threshold(normalized)
        if threshold:
            consumed.append(threshold.span)
            if not plan.dimensions:
                return self._clarify(plan, "missing_analysis_dimension", "阈值或“高于平均”需要按某个维度分组比较，请补充分组维度。", 0.35, self._dimension_options(tables))
            plan.having = HavingSpec(
                threshold.operator, threshold.mode, threshold.value, threshold.span,
                "与各分组聚合值的平均比较" if threshold.mode == "scalar_avg" else f"聚合值 {threshold.operator} {threshold.value:g}",
            )
        if plan.comparison_mode != "none" and plan.having is not None:
            return self._clarify(plan, "unsupported_comparison_having", "同比/环比暂不叠加聚合阈值，请先查询比较结果后再筛选。", 0.25)

        # ---- JOIN
        required_tables = {
            plan.metric_table,
            *(plan.dimension_tables.get(column, table.name) for column in plan.dimensions),
            *(item.table or table.name for item in plan.filters),
        }
        if plan.comparison_mode != "none":
            required_tables.add(plan.comparison_period["date_table"])
        required_tables.discard(None)
        via = {name: qualifier_table for name in required_tables if qualifier_table and name != qualifier_table} if qualifier_table else None
        if qualifier_table:
            required_tables.add(qualifier_table)
        joins = self._resolve_joins(table.name, required_tables, tables, path_hint=path_hint, via=via)
        if joins is None:
            plan.clarification = "识别到跨表字段，但当前 Schema 没有可验证的外键 JOIN 路径。"
            plan.clarification_code = "unreachable_join_path"
            plan.confidence = 0.28
            return plan
        plan.join_tables, plan.join_conditions, plan.join_path, plan.join_alternatives = joins
        if plan.join_alternatives:
            return self._clarify(
                plan, "ambiguous_join_path", "识别到多条同样合理的外键 JOIN 路径，请选择要使用的业务关系。", 0.3,
                [{"value": item["signature"], "label": item["label"]} for item in plan.join_alternatives],
            )
        if plan.join_path:
            plan.assumptions.append("外键验证 JOIN 路径：" + " -> ".join([table.name, *[item["to_table"] for item in plan.join_path]]))
            plan.fan_out = any(item.get("cardinality") == "one_to_many" for item in plan.join_path)
        if plan.fan_out:
            if plan.metric_function in {"SUM", "AVG"}:
                from .metric_compiler import MetricCompiler, MetricPlanError
                try:
                    MetricCompiler(self).compile(plan, tables)
                except MetricPlanError as exc:
                    return self._clarify(plan, exc.code, str(exc), 0.3)
                plan.assumptions.append("子表筛选按事实主键半连接，避免重复求和/平均")
            if plan.metric_function == "COUNT":
                plan.metric_function = "COUNT_DISTINCT"
                plan.assumptions.append("关联路径含一对多关系，计数使用 COUNT(DISTINCT 主键) 避免重复")
        self._role_assumptions(plan, tables, qualifier_table)
        plan.limit = _MAX_LIMIT

        if '趋势' in normalized and plan.comparison_mode == "none" and not any(
            plan.dimension_transforms.get(column) for column in plan.dimensions
        ):
            if plan.filters:
                return self._clarify(plan,'missing_time_grain','已识别筛选范围，请选择按月还是按年展示趋势。',0.4,
                    [{'value':'monthly_trend','label':'按月趋势'},{'value':'yearly_trend','label':'按年趋势'}])
            return self._clarify(
                plan, "missing_time_range", "趋势查询需要时间范围或时间粒度，请指定年份、月份或按月趋势。", 0.4,
                [{"value": "year", "label": "指定年份"}, {"value": "month", "label": "指定月份"}, {"value": "monthly_trend", "label": "按月趋势"}],
            )
        if "趋势" in normalized:
            consumed.append("趋势")
        if any(word in normalized for word in ("对比", "比较")) and not plan.dimensions and not plan.filters:
            return self._clarify(
                plan, "missing_comparison_scope", "这个问题需要比较维度或时间范围，请补充例如按地区、按渠道或具体年份。", 0.42,
                [*self._dimension_options(tables, prefix="按", suffix="比较"), {"value": "time_range", "label": "指定时间范围"}],
            )

        # ---- 覆盖率守卫
        consumed.extend(self._selected_schema_subjects(clean_question, plan, tables))
        # Presentation verbs are harmless only after the requested selection
        # operation was actually represented. Keep the rank/Top-N predicate.
        if plan.dimensions and (plan.top_n is not None or plan.analysis_mode == 'rank'):
            consumed.extend(match.group(0) for match in re.finditer(r'(?:并且|并)?找出', normalized))
        if plan.dimensions and plan.top_n is not None:
            consumed.extend(cue for cue in ('取最高', '取最低', '含并列') if cue in normalized)
        if any(self._is_date_column(link.column, self._schema_column_type(tables, link.table, link.column))
               for link in links) and (plan.filters or plan.dimensions) and '对应' in normalized:
            # 日期字段→指标 is a syntactic bridge only after both are linked;
            # arbitrary association requests still leave their objects intact.
            consumed.append('对应')
        if normalize_text(plan.table or '') in normalized:
            topic = re.match(r'^(?:换个主题|换一个主题|换个问题)[,，:：]', normalized)
            if topic:
                consumed.append(topic.group(0))
        unresolved = self._coverage_guard(normalized, consumed)
        plan.coverage = {
            "consumed": sorted({item for item in consumed if item}),
            "unresolved": unresolved,
            "ignored_instruction_spans": ignored_instructions,
        }
        if unresolved:
            return self._clarify(
                plan, "unresolved_terms",
                f"问题中的“{'、'.join(unresolved)}”暂无法安全解析为查询条件。请改写为明确的时间、取值或阈值，避免给出错误结果。", 0.3,
            )

        evidence = 0.42 + min(0.28, 0.06 * len(links))
        if plan.filters:
            evidence += 0.1
        if plan.dimensions:
            evidence += 0.08
        plan.confidence = round(min(0.98, evidence), 3)
        plan.assumptions.append(f"使用表 {table.name} 的只读聚合查询")
        return plan

    # ================================================================ helpers: planning
    @staticmethod
    def _schema_column_type(tables, table_name, column_name):
        return next((column.data_type for table in tables if table.name == table_name
                     for column in table.columns if column.name == column_name), '')

    @staticmethod
    def _selected_schema_subjects(question, plan, tables):
        """Consume only native names backed by the already chosen query graph.

        Column-name substrings are not separate subject mentions. English names
        require identifier boundaries in the original question (before spaces
        are removed by coverage normalization).
        """
        normalized = normalize_text(question)
        selected = {plan.table, plan.metric_table, *plan.join_tables}
        subjects = []
        for table in tables:
            native = normalize_text(table.name)
            if table.name not in selected or len(native) < 2:
                continue
            if re.fullmatch(r'[a-z0-9_]+', native) and not re.search(
                    r'(?<![A-Za-z0-9_])' + re.escape(table.name) + r'(?![A-Za-z0-9_])', question, re.IGNORECASE):
                continue
            column_spans = [match.span() for candidate in tables for column in candidate.columns
                            if len(normalize_text(column.name)) > len(native)
                            for match in re.finditer(re.escape(normalize_text(column.name)), normalized)]
            positions = list(re.finditer(re.escape(native), normalized))
            if any(not any(start <= match.start() and match.end() <= end for start, end in column_spans)
                   and not lexicon.NEGATION_BEFORE.search(normalized[max(0, match.start()-8):match.start()])
                   for match in positions):
                subjects.append(native)
        return subjects

    @staticmethod
    def _clarify(plan: QueryPlan, code: str, message: str, confidence: float, options: list[dict[str, str]] | None = None) -> QueryPlan:
        plan.clarification = message
        plan.clarification_code = code
        plan.clarification_options = list(options or [])
        plan.confidence = confidence
        return plan

    def _metric_options(self, tables: tuple[TableInfo, ...]) -> list[dict[str, str]]:
        names = {table.name for table in tables}
        options: list[dict[str, str]] = []
        origins: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for rule in self.linker.rules_for(tables):
            if rule.role == "metric" and rule.table in names and (rule.table, rule.column) not in seen and rule.aliases:
                seen.add((rule.table, rule.column))
                options.append({"value": rule.column, "label": rule.aliases[0]})
                origins.append((rule.table,rule.column))
        if not options:
            for table in tables:
                for column in table.columns:
                    if any(kind in column.data_type.upper() for kind in ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT")) and not column.primary_key:
                        options.append({"value": column.name, "label": column.name})
                        origins.append((table.name,column.name))
        # Equal names in different tables are different choices. Preserve the
        # short legacy values only when both field and displayed label are unique.
        value_counts = Counter(option['value'] for option in options)
        label_counts = Counter(option['label'] for option in options)
        for index,option in enumerate(options):
            if value_counts[option['value']]>1 or label_counts[option['label']]>1:
                table,column = origins[index]
                option['value'] = f'{table}.{column}'
                option['label'] = f"{option['label']}（{table}.{column}）"
        return options[:6]

    def _dimension_options(self, tables: tuple[TableInfo, ...], *, prefix: str = "按", suffix: str = "") -> list[dict[str, str]]:
        names = {table.name for table in tables}
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for rule in self.linker.rules_for(tables):
            if rule.role == "dimension" and rule.table in names and rule.column not in seen and rule.aliases and not self._is_date_column(rule.column, self._schema_column_type(tables, rule.table, rule.column)):
                seen.add(rule.column)
                options.append({"value": rule.column, "label": f"{prefix}{rule.aliases[0]}{suffix}"})
        return options[:6]

    def _column_hint(self, table: str, column: str) -> str:
        rule = self.linker.rule_for(table, column)
        return rule.aliases[0] if rule and rule.aliases else column

    @staticmethod
    def _filters_from_values(normalized: str, matches: list) -> tuple[list[FilterSpec], list[str]]:
        spans: list[str] = []
        grouped: dict[tuple[str, str, bool], list[Any]] = {}
        order: list[tuple[str, str, bool]] = []
        ordered_matches = sorted(matches, key=lambda item: item.start)
        # 否定词的作用域可以覆盖并列取值："除了华东和华南"、
        # "华东、华南以外" 都应生成一个 NOT IN，而不能把第二个值
        # 当成正向过滤。只允许明确的并列连接词传播作用域，避免
        # "不含华东但包含华南" 被错误合并。
        negated_matches: set[int] = set()
        by_column: dict[tuple[str, str], list[Any]] = {}
        for item in ordered_matches:
            by_column.setdefault((item.table, item.column), []).append(item)
        for column_matches in by_column.values():
            explicit: dict[int, bool] = {}
            for item in column_matches:
                before = lexicon.NEGATION_BEFORE.search(normalized[max(0, item.start - 8):item.start])
                after = lexicon.NEGATION_AFTER.search(normalized[item.end:item.end + 4])
                explicit[id(item)] = bool(before)
                if before:
                    spans.append(before.group(0))
                if after:
                    # 末值后的“以外/之外”是整段并列值的否定标记。
                    spans.append(after.group(0))
                    explicit[id(item)] = True
            for index, item in enumerate(column_matches):
                if explicit[id(item)]:
                    negated_matches.add(id(item))
                    continue
                if index and id(column_matches[index - 1]) in negated_matches:
                    separator = normalized[column_matches[index - 1].end:item.start]
                    if separator and re.fullmatch(r"[和与及、,，或以及\s]+", separator):
                        negated_matches.add(id(item))
            # 若否定标记出现在并列组最后一个值之后，将作用域向前传播。
            for index, item in reversed(list(enumerate(column_matches))):
                after = lexicon.NEGATION_AFTER.search(normalized[item.end:item.end + 4])
                if not after:
                    continue
                negated_matches.add(id(item))
                for previous in reversed(column_matches[:index]):
                    separator = normalized[previous.end:item.start]
                    if not separator or not re.fullmatch(r"[和与及、,，或以及\s]+", separator):
                        break
                    negated_matches.add(id(previous))
                    item = previous

        for match in ordered_matches:
            negated = id(match) in negated_matches
            spans.append(match.span)
            key = (match.table, match.column, negated)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            if match.value not in [m.value for m in grouped[key]]:
                grouped[key].append(match)
        filters: list[FilterSpec] = []
        for table, column, negated in order:
            items = grouped[(table, column, negated)]
            values = [item.value for item in items]
            source = "、".join(item.span for item in items)
            if len(values) == 1:
                operator, value = ("!=" if negated else "="), values[0]
            else:
                operator, value = ("NOT IN" if negated else "IN"), tuple(values)
            verb = "排除" if negated else "限定"
            filters.append(FilterSpec(column, operator, value, source, f"{verb} {column} ∈ {{{', '.join(values)}}}", table))
        return filters, spans

    def _coverage_guard(self, normalized: str, consumed: list[str]) -> list[str]:
        residual = normalized
        for span in sorted({item for item in consumed if item}, key=len, reverse=True):
            residual = residual.replace(span, "\x00")
        unresolved = [match.group(0) for match in lexicon.UNRESOLVED_CUES.finditer(residual)]
        structural = (*_COVERAGE_STRUCTURAL_WORDS, *lexicon.FILLER_WORDS)
        def content(fragment):
            for word in sorted(structural, key=len, reverse=True):
                fragment = fragment.replace(word, '')
            return fragment
        for match in re.finditer(r"\x00(?:和|与|及|、|或)([\u3400-\u9fff]{2,})|([\u3400-\u9fff]{2,})(?:和|与|及|、|或)\x00", residual):
            fragment = match.group(1) or match.group(2) or ""
            # 并列结构中未被识别的实体：截掉"的/各/按…"之后的修饰部分再判断
            fragment = re.split(r"的|各|按|统计|每个|分别|是|有", fragment)[0 if match.group(1) else -1]
            fragment = content(fragment)
            if len(fragment) >= 2:
                unresolved.append(fragment)
        # 最后检查未被任何槽位消费的中文实体片段。只移除通用句法/业务
        # 结构词；剩余片段宁可澄清，也不能像“摇滚的订单金额”一样静默
        # 退化成全量汇总。真实取值应通过 ValueIndex 或显式同义词消费。
        for match in re.finditer(r"[\u3400-\u9fff]{2,}", residual):
            fragment = content(match.group(0))
            if len(fragment) >= 2:
                unresolved.append(fragment)
        return list(dict.fromkeys(unresolved))

    def _many_to_one_distances(self, base: str | None, tables: tuple[TableInfo, ...]) -> dict[str, int]:
        by_name = {table.name: table for table in tables}
        if base not in by_name:
            return {}
        distances = {base: 0}
        queue = deque([base])
        while queue:
            current = queue.popleft()
            for fk in by_name[current].foreign_keys:
                if fk.table in by_name and fk.table not in distances:
                    distances[fk.table] = distances[current] + 1
                    queue.append(fk.table)
        return distances

    def _bind_date_column(self, links, tables, metric_table, reachable, plan: QueryPlan) -> tuple[str, str] | None:
        by_name = {table.name: table for table in tables}
        explicit = [link for link in links if link.role == "dimension"
                    and self._is_date_column(link.column, self._schema_column_type(tables, link.table, link.column))]
        specific = {(link.table, link.column) for link in explicit
                    if normalize_text(link.matched_alias) not in _GENERIC_TIME_ALIASES}
        if len(specific) == 1:
            return next(iter(specific))
        if len(specific) > 1:
            self._ambiguous_date_role(plan, sorted(specific))
            return None
        candidates = sorted(
            (distance, name, column.name)
            for name, distance in reachable.items()
            for column in by_name[name].columns
            if self._is_date_column(column.name, column.data_type)
        )
        if not candidates:
            return None
        nearest = [(name, column) for distance, name, column in candidates if distance == candidates[0][0]]
        if len(nearest) > 1:
            self._ambiguous_date_role(plan, nearest)
            return None
        _, name, column = candidates[0]
        if explicit:
            plan.assumptions.append(f"通用时间词绑定到指标主题的日期字段 {name}.{column}")
        elif candidates[0][0] > 0:
            plan.assumptions.append(f"时间条件使用与指标多对一关联的日期字段 {name}.{column}")
        return name, column

    def _ambiguous_date_role(self, plan, roles):
        return self._clarify(plan, 'ambiguous_date_column',
            '存在多个同样可用的日期字段，请明确按哪个时间口径统计。', 0.3,
            [{'value': f'{table}.{column}', 'label': f'{table}.{column}'} for table, column in roles])

    @staticmethod
    def _date_parameters(binding, tables, connection, start: str, end: str, plan: QueryPlan, *, cache=None, cache_namespace=None):
        """返回绑定和边界格式；epoch 秒/毫秒使用数值参数，ISO 保持文本。"""
        if binding is None:
            return None, (start, end, "unknown")
        table_name, column_name = binding
        cache_key = ((cache_namespace, table_name, column_name) if cache_namespace is not None else (table_name, column_name))
        if cache is not None and cache_key in cache:
            storage_format, _ = cache[cache_key]
            if storage_format == "iso_text":
                return binding, (start, end, storage_format)
            if storage_format == "unix_epoch_seconds":
                scale = 1
            elif storage_format == "unix_epoch_milliseconds":
                scale = 1000
            else:
                return None, (start, end, "unknown")
            start_value = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * scale)
            end_value = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() * scale)
            return binding, (start_value, end_value, storage_format)
        column = next((c for t in tables if t.name == table_name for c in t.columns if c.name == column_name), None)
        if column is None:
            return binding, (start, end, "iso_text")
        # 用单行聚合探针覆盖整列，避免 LIMIT 20 恰好漏掉后续混合格式；
        # 不读取完整列，保持大表查询的固定结果传输成本。
        quoted_table = _quote(table_name)
        quoted_column = _quote(column_name)
        type_rows = connection.execute(
            f'SELECT typeof({quoted_column}) AS value_type, COUNT(*) AS count '
            f'FROM {quoted_table} WHERE {quoted_column} IS NOT NULL GROUP BY typeof({quoted_column})'
        ).fetchall()
        kinds = {str(value_type) for value_type, _count in type_rows}
        numeric_kinds = kinds & {"integer", "real"}
        text_present = "text" in kinds
        other_present = kinds - numeric_kinds - {"text"}
        numeric_seconds = numeric_milliseconds = 0
        numeric_values: list[float] = []
        if numeric_kinds:
            numeric_probe = connection.execute(
                f'SELECT '
                f'SUM(CASE WHEN ABS(CAST({quoted_column} AS REAL)) >= 100000000 AND ABS(CAST({quoted_column} AS REAL)) < 100000000000 THEN 1 ELSE 0 END), '
                f'SUM(CASE WHEN ABS(CAST({quoted_column} AS REAL)) >= 100000000000 AND ABS(CAST({quoted_column} AS REAL)) < 100000000000000 THEN 1 ELSE 0 END), '
                f'MIN(CAST({quoted_column} AS REAL)), MAX(CAST({quoted_column} AS REAL)) '
                f'FROM {quoted_table} WHERE {quoted_column} IS NOT NULL AND typeof({quoted_column}) IN (\'integer\', \'real\')'
            ).fetchone()
            numeric_seconds, numeric_milliseconds = (int(numeric_probe[0] or 0), int(numeric_probe[1] or 0))
            numeric_values = [float(item) for item in numeric_probe[2:] if item is not None]
        text_values: list[Any] = []
        if text_present:
            sample = connection.execute(
                f'SELECT {quoted_column} FROM {quoted_table} '
                f'WHERE {quoted_column} IS NOT NULL AND typeof({quoted_column}) = \'text\' LIMIT 20'
            ).fetchall()
            text_values = [row[0] for row in sample]
        values: list[Any] = [*numeric_values, *text_values, *other_present]
        if numeric_seconds and numeric_milliseconds:
            values.extend((1e9, 1e12))
        numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numeric or any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            # Unix seconds约在 2000-2100 年为 1e9，毫秒为 1e12；混合格式拒绝。
            if len(numeric) != len(values):
                return None, (start, end, "unknown")
            if all(1e8 <= abs(value) < 1e11 for value in numeric):
                scale, label = 1, "unix_epoch_seconds"
            elif all(1e11 <= abs(value) < 1e14 for value in numeric):
                scale, label = 1000, "unix_epoch_milliseconds"
            else:
                return None, (start, end, "unknown")
            start_value = datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * scale
            end_value = datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() * scale
            start_value = int(start_value)
            end_value = int(end_value)
            plan.assumptions.append(f"检测到 {table_name}.{column_name} 使用 {label}")
            if cache is not None:
                cache[cache_key] = (label, (start_value, end_value, label))
            return binding, (start_value, end_value, label)
        if values and all(isinstance(value, str) for value in values):
            try:
                for value in values:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None, (start, end, "unknown")
            if cache is not None:
                cache[cache_key] = ("iso_text", (start, end, "iso_text"))
            return binding, (start, end, "iso_text")
        if values:
            # 非空样本混合文本/数值或其它 SQLite 类型，不能猜测比较语义。
            return None, (start, end, "unknown")
        if not values and column.data_type.upper() in {"TEXT", "DATE", "DATETIME", "TIMESTAMP"}:
            return binding, (start, end, "iso_text")
        if not values and any(token in column.data_type.upper() for token in ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE")):
            return None, (start, end, "unknown")
        if cache is not None:
            cache[cache_key] = ("iso_text", (start, end, "iso_text"))
        return binding, (start, end, "iso_text")

    @classmethod
    def normalize_comparison_period(cls, period, tables, connection, plan, *, date_storage_cache=None, date_storage_cache_namespace=None):
        """按实际列存储格式规范化比较窗口；模型计划与规则计划共用该门控。"""
        required = {"date_table", "date_column", "current_start", "current_end", "previous_start", "previous_end"}
        if not required <= set(period):
            return None, "unknown"
        binding = (period["date_table"], period["date_column"])
        converted, params = cls._date_parameters(
            binding, tables, connection, period["current_start"], period["current_end"], plan,
            cache=date_storage_cache,
            cache_namespace=date_storage_cache_namespace,
        )
        if converted is None:
            return None, "unknown"
        scale = 1 if params[2] == "iso_text" else (1000 if params[2] == "unix_epoch_milliseconds" else 1)

        def convert(value: str):
            if params[2] == "iso_text":
                return value
            return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * scale)

        result = dict(period)
        for field in ("current_start", "current_end", "previous_start", "previous_end"):
            result[f"{field}_text"] = period[field]
            result[field] = convert(period[field])
        return result, params[2]

    @classmethod
    def _comparison_parameters(cls, period, tables, connection, plan, *, date_storage_cache=None, date_storage_cache_namespace=None):
        return cls.normalize_comparison_period(
            period, tables, connection, plan, date_storage_cache=date_storage_cache,
            date_storage_cache_namespace=date_storage_cache_namespace,
        )

    def _rebind_date_dimensions(self, plan: QueryPlan, tables, reachable, links=()) -> None:
        by_name = {table.name: table for table in tables}
        for column in list(plan.dimensions):
            source_table = plan.dimension_tables.get(column, plan.table)
            if not self._is_date_column(column, self._schema_column_type(tables, source_table, column)) or source_table in reachable:
                continue
            if any(link.table == source_table and link.column == column
                   and normalize_text(link.matched_alias) not in _GENERIC_TIME_ALIASES for link in links):
                # A named role is business intent, even outside the metric's
                # many-to-one neighborhood. Join safety must validate that role;
                # replacing it with a nearer date would change the question.
                continue
            candidates = sorted(
                (distance, name, info.name)
                for name, distance in reachable.items()
                for info in by_name[name].columns
                if self._is_date_column(info.name, info.data_type)
            )
            if not candidates:
                continue
            nearest = [(name, name_column) for distance, name, name_column in candidates if distance == candidates[0][0]]
            if len(nearest) > 1:
                self._ambiguous_date_role(plan, nearest)
                continue
            _, name, new_column = candidates[0]
            index = plan.dimensions.index(column)
            plan.dimensions[index] = new_column
            plan.dimension_tables.pop(column, None)
            plan.dimension_tables[new_column] = name
            if column in plan.dimension_transforms:
                plan.dimension_transforms[new_column] = plan.dimension_transforms.pop(column)
            if column in plan.dimension_labels:
                plan.dimension_labels[new_column] = plan.dimension_labels.pop(column)
            plan.assumptions.append(f"时间维度绑定到指标主题的日期字段 {name}.{new_column}")

    @staticmethod
    def _comparison_period(mode: str, current, binding: tuple[str, str] | None) -> dict[str, str] | None:
        if current is None or binding is None:
            return None
        shift = {"同比": {"year": 12, "half": 12, "quarter": 12, "month": 12, "range": 12},
                 "环比": {"month": 1, "quarter": 3, "half": 6}}[mode].get(current.granularity)
        if shift is None:
            return None
        previous_start = lexicon._add_months(current.start, -shift)
        previous_end = lexicon._add_months(current.end, -shift) if current.end.day == 1 else None
        if previous_end is None or previous_end > current.start:
            return None
        return {
            "date_table": binding[0], "date_column": binding[1],
            "current_start": current.start.isoformat(), "current_end": current.end.isoformat(),
            "previous_start": previous_start.isoformat(), "previous_end": previous_end.isoformat(),
        }

    def _role_assumptions(self, plan: QueryPlan, tables, qualifier_table) -> None:
        """同一维表被多条外键引用（订单地区 vs 客户地区）时，把所选角色写入假设。"""

        by_name = {table.name: table for table in tables}
        joined = {plan.table, *plan.join_tables}
        for step in plan.join_path:
            target = step["to_table"]
            referrers = sorted({
                source.name for source in tables for fk in source.foreign_keys
                if fk.table == target and source.name != target and source.name in self._many_to_one_distances(plan.table, tables)
            })
            if len(referrers) > 1 and target in {plan.dimension_tables.get(c) for c in plan.dimensions} | {f.table for f in plan.filters}:
                used = step["from_table"]
                others = [name for name in referrers if name != used]
                plan.assumptions.append(
                    f"{target} 可经由 {'、'.join(referrers)} 关联；本次使用 {used} 的外键（如需按{'、'.join(others)}口径，请在问题中写明“{self._table_hint(others[0], tables)}所在…”）"
                )
        del by_name, joined, qualifier_table

    def _table_hint(self, table: str, tables: tuple[TableInfo, ...]) -> str:
        rule = next((rule for rule in self.linker.rules_for(tables) if rule.table == table and rule.role == "dimension" and rule.aliases), None)
        return rule.aliases[0] if rule else table

    # ================================================================ SQL builder
    def build_sql(self, plan: QueryPlan) -> tuple[str, tuple[Any, ...]]:
        if plan.clarification:
            raise ValueError("存在待澄清问题，不能生成 SQL")
        if not plan.table or not plan.metric_column or not plan.metric_function:
            raise ValueError("查询计划缺少表或指标")
        if plan.comparison_mode != "none":
            return self._build_comparison_sql(plan)

        dimensions, groups = self._dimension_sql(plan)
        metric_expression = self._metric_expression(plan)
        metric_label = plan.metric_label or plan.metric_column
        from_sql = self._from_sql(plan)
        where_sql, where_params = self._where_sql(plan)
        direction = "DESC" if plan.order_desc else "ASC"

        select_parts = [*dimensions, f"{metric_expression} AS {_quote(metric_label)}"]
        use_top_n = bool(plan.top_n and plan.dimensions)
        if plan.analysis_mode == "rank" or use_top_n:
            select_parts.append(f'DENSE_RANK() OVER (ORDER BY {metric_expression} {direction}) AS "排名"')
        if plan.analysis_mode == "share":
            select_parts.append(f'(100.0 * {metric_expression} / NULLIF(SUM({metric_expression}) OVER (), 0)) AS "占比(%)"')

        parameters: list[Any] = list(where_params)
        sql = f"SELECT {', '.join(select_parts)} {from_sql}{where_sql}"
        if groups:
            sql += " GROUP BY " + ", ".join(groups)
        if plan.having:
            if plan.having.mode == "scalar_avg":
                inner = f"SELECT {metric_expression} AS \"__group_metric\" {from_sql}{where_sql}"
                if groups:
                    inner += " GROUP BY " + ", ".join(groups)
                sql += f' HAVING {metric_expression} {plan.having.operator} (SELECT AVG("__group_metric") FROM ({inner}) AS avg_base)'
                parameters.extend(where_params)
            else:
                sql += f" HAVING {metric_expression} {plan.having.operator} ?"
                parameters.append(plan.having.value)
        if use_top_n:
            # 外层只投影原有列：Top-N 用 DENSE_RANK 保留并列，但不改变非排名模式的列契约
            visible = [_quote(plan.dimension_labels.get(column, column)) for column in plan.dimensions] + [_quote(metric_label)]
            if plan.analysis_mode in {"rank", "share"}:
                visible.append('"排名"' if plan.analysis_mode == "rank" else '"占比(%)"')
            sql = f'SELECT {", ".join(visible)} FROM ({sql}) AS ranked WHERE "排名" <= ? ORDER BY "排名" ASC, {_quote(metric_label)} {direction} LIMIT ?'
            parameters.extend([plan.top_n, plan.limit])
            return sql, tuple(parameters)
        if plan.analysis_mode == "rank":
            sql += f' ORDER BY "排名" ASC, {_quote(metric_label)} {direction}'
        elif plan.analysis_mode == 'aggregate' and '趋势' in plan.rewritten_question and any(
                plan.dimension_transforms.get(column) in {'month','year'} for column in plan.dimensions):
            time_groups = [expression for column, expression in zip(plan.dimensions, groups)
                           if plan.dimension_transforms.get(column) in {'month','year'}]
            other_groups = [expression for column, expression in zip(plan.dimensions, groups)
                            if plan.dimension_transforms.get(column) not in {'month','year'}]
            sql += ' ORDER BY ' + ', '.join(expression+' ASC' for expression in time_groups+other_groups)
        else:
            sql += f" ORDER BY {metric_expression} {direction}"
        sql += " LIMIT ?"
        parameters.append(plan.limit)
        return sql, tuple(parameters)

    @staticmethod
    def _qualified(table: str, column: str) -> str:
        return f"{_quote(table)}.{_quote(column)}"

    def _dimension_sql(self, plan: QueryPlan) -> tuple[list[str], list[str]]:
        selects, groups = [], []
        for column in plan.dimensions:
            expression = self._qualified(plan.dimension_tables.get(column, plan.table), column)
            transform = plan.dimension_transforms.get(column, "raw")
            if transform == "month":
                expression = f"strftime('%Y-%m', {expression})"
            elif transform == "year":
                expression = f"strftime('%Y', {expression})"
            label = plan.dimension_labels.get(column, column)
            selects.append(f"{expression} AS {_quote(label)}" if label != column or transform != "raw" else expression)
            groups.append(expression)
        return selects, groups

    def _metric_expression(self, plan: QueryPlan) -> str:
        column = self._qualified(plan.metric_table or plan.table, plan.metric_column)
        if plan.metric_function == "COUNT":
            return f"COUNT(DISTINCT {column})" if plan.fan_out else "COUNT(*)"
        if plan.metric_function == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {column})"
        return f"{plan.metric_function}({column})"

    def _from_sql(self, plan: QueryPlan) -> str:
        sql = f"FROM {_quote(plan.table)}"
        for join_table, condition in zip(plan.join_tables, plan.join_conditions):
            sql += f" JOIN {_quote(join_table)} ON {condition}"
        return sql

    def _where_sql(self, plan: QueryPlan, extra: list[str] | None = None) -> tuple[str, list[Any]]:
        clauses: list[str] = list(extra or [])
        parameters: list[Any] = []
        for item in plan.filters:
            target = self._qualified(item.table or plan.table, item.column)
            if item.operator == "RANGE":
                clauses.append(f"{target} >= ? AND {target} < ?")
                parameters.extend(item.value)
            elif item.operator == "BETWEEN":
                # SQL 标准语义：两端闭区间（模型计划契约 v1 的 BETWEEN）
                clauses.append(f"{target} >= ? AND {target} <= ?")
                parameters.extend(item.value)
            elif item.operator in {"IN", "NOT IN"}:
                values = list(item.value)
                clauses.append(f"{target} {item.operator} ({', '.join('?' for _ in values)})")
                parameters.extend(values)
            else:
                clauses.append(f"{target} {item.operator} ?")
                parameters.append(item.value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters

    def _build_comparison_sql(self, plan: QueryPlan) -> tuple[str, tuple[Any, ...]]:
        period = plan.comparison_period
        required = {"date_table", "date_column", "current_start", "current_end", "previous_start", "previous_end"}
        if plan.comparison_mode not in {"同比", "环比"} or not required <= set(period):
            raise ValueError("比较计划缺少合法时间窗口")
        date_expression = self._qualified(period["date_table"], period["date_column"])
        metric_column = self._qualified(plan.metric_table or plan.table, plan.metric_column)

        def period_expression(start: str, end: str) -> tuple[str, tuple[Any, ...]]:
            condition = f"{date_expression} >= ? AND {date_expression} < ?"
            function = plan.metric_function
            if function == "COUNT":
                if plan.fan_out:
                    return f"COUNT(DISTINCT CASE WHEN {condition} THEN {metric_column} END)", (start, end)
                return f"COUNT(CASE WHEN {condition} THEN 1 END)", (start, end)
            if function == "COUNT_DISTINCT":
                return f"COUNT(DISTINCT CASE WHEN {condition} THEN {metric_column} END)", (start, end)
            # 无 ELSE：该周期没有数据时得到 NULL，而不是伪造的 0
            return f"{function}(CASE WHEN {condition} THEN {metric_column} END)", (start, end)

        dimensions, groups = self._dimension_sql(plan)
        outer = [_quote(plan.dimension_labels.get(column, column)) for column in plan.dimensions]
        name = plan.metric_label or plan.metric_column or "指标"
        current_label = ("本期" if plan.comparison_mode == "同比" else "当前") + name
        previous_label = ("同期" if plan.comparison_mode == "同比" else "上期") + name
        change_label = "同比(%)" if plan.comparison_mode == "同比" else "环比(%)"
        current_expression, current_parameters = period_expression(period["current_start"], period["current_end"])
        previous_expression, previous_parameters = period_expression(period["previous_start"], period["previous_end"])
        dims_sql = [f'{item} AS {_quote(plan.dimension_labels.get(column, column))}' if " AS " not in item else item
                    for item, column in zip(dimensions, plan.dimensions)]
        inner_select = [*dims_sql, f"{current_expression} AS {_quote(current_label)}", f"{previous_expression} AS {_quote(previous_label)}"]
        where_sql, where_params = self._where_sql(plan, [f"{date_expression} >= ? AND {date_expression} < ?"])
        parameters: list[Any] = [*current_parameters, *previous_parameters, period["previous_start"], period["current_end"], *where_params]
        inner_sql = f"SELECT {', '.join(inner_select)} {self._from_sql(plan)}{where_sql}"
        if groups:
            inner_sql += " GROUP BY " + ", ".join(groups)
        prefix = (", ".join(outer) + ", ") if outer else ""
        outer_sql = (
            f"SELECT {prefix}{_quote(current_label)}, {_quote(previous_label)}, "
            f"(100.0 * ({_quote(current_label)} - {_quote(previous_label)}) / NULLIF({_quote(previous_label)}, 0)) AS {_quote(change_label)} "
            f"FROM ({inner_sql}) AS comparison_base "
            f"ORDER BY {_quote(current_label)} {'DESC' if plan.order_desc else 'ASC'} LIMIT ?"
        )
        parameters.append(plan.limit)
        return outer_sql, tuple(parameters)

    # ================================================================ legacy-compatible helpers
    def resolve_joins(self, base_table, required_tables, tables, *, path_hint=None):
        """公开 JOIN 图解析入口，供模型计划校验复用同一实现。"""

        return self._resolve_joins(base_table, required_tables, tables, path_hint=path_hint)

    @staticmethod
    def _analysis_mode(question: str) -> str:
        if any(word in question for word in ("排名", "排行", "名次")):
            return "rank"
        if any(word in question for word in ("占比", "份额", "比例")):
            return "share"
        return "aggregate"

    @staticmethod
    def _choose_table(question: str, tables: tuple[TableInfo, ...], links: list) -> TableInfo | None:
        if not tables:
            return None
        if len(tables) == 1:
            return tables[0]
        metric_links = [link for link in links if link.role == "metric"]
        if metric_links:
            return next((table for table in tables if table.name == metric_links[0].table), None)
        if links:
            return next((table for table in tables if table.name == links[0].table), None)
        for table in tables:
            if normalize_text(table.name) in question:
                return table
        return None

    def _choose_metric(self, links: list, question: str, table: TableInfo) -> tuple[str, str, str, str] | None:
        metric_links = [link for link in links if link.role == "metric" and link.table == table.name]
        if metric_links:
            link = metric_links[0]
            label = {
                "sales_amount": "销售额", "quantity": "销量", "unit_price": "单价", "order_id": "订单数",
                "line_amount": "销售额", "item_id": "明细数", "resolution_hours": "解决时长",
            }.get(link.column, link.column)
            # An explicit local count applies to this named source field, not
            # to a neighboring amount or an arbitrary entity from the schema.
            local = self._metric_clause(question, link)
            if '去重计数' in local:
                return link.table, link.column, 'COUNT_DISTINCT', f'去重数{label}'
            if re.search(r'(?:记录计数|记录数|计数)', local):
                return link.table, link.column, 'COUNT', f'记录数{label}'
            if link.column == "customer_id":
                return link.table, link.column, "COUNT_DISTINCT", "客户数"
            if link.column.lower().replace("_", "").endswith("id") and any(word in link.matched_alias for word in ("数", "数量", "笔", "量")):
                function = "COUNT" if link.table == table.name and '去重' not in question else "COUNT_DISTINCT"
                return link.table, link.column, function, label
            average_threshold = _AVERAGE_THRESHOLD_RE.search(question)
            explicit_aggregate = next((
                (function, label_prefix)
                for cues, function, label_prefix in _AGGREGATE_CUES
                if any(cue in question for cue in cues)
                and not (function == "AVG" and average_threshold)
            ), None)
            if explicit_aggregate:
                function, label_prefix = explicit_aggregate
                return link.table, link.column, function, f"{label_prefix}{label}"
            average_requested = any(word in question for word in ("平均", "均价", "均值", "均分", "平均值"))
            if average_requested and not average_threshold:
                return link.table, link.column, "AVG", f"平均{label}"
            if link.metric_function:
                return link.table, link.column, link.metric_function, (
                    f"平均{label}" if link.metric_function=='AVG' and not label.startswith('平均') else label)
            return link.table, link.column, "SUM", label
        if any(word in question for word in ("多少订单", "几笔订单", "订单量")) and any(c.name == "order_id" for c in table.columns):
            return table.name, "order_id", "COUNT", "订单数"
        return None

    @staticmethod
    def _metric_clause(question, link):
        alias = normalize_text(link.matched_alias)
        position = question.find(alias)
        if position < 0:
            return question
        separators = r'(?:以及|同时|和|及|与|、|,|，|;|；)'
        before = re.split(separators, question[:position])[-1]
        after = re.split(separators, question[position + len(alias):])[0]
        return before + alias + after

    @staticmethod
    def _count_cue_text(question):
        # The shorter 计数 cue must not falsely make 去重计数 a second COUNT.
        return question.replace('去重计数', '')

    @classmethod
    def _aggregate_matches(cls, question):
        return [(cue, function) for cues, function, _ in _AGGREGATE_CUES
                for cue in cues if cue in (cls._count_cue_text(question) if function == 'COUNT' else question)]

    def _choose_dimensions(self, links: list, question: str, table: TableInfo, metric_column: str, all_links: list | None = None, *, tables=None):
        tables = tuple(tables) if tables is not None else (table,)
        seen: set[str] = set()
        dimensions: list[str] = []
        dimension_tables: dict[str, str] = {}
        dimension_transforms: dict[str, str] = {}
        dimension_labels: dict[str, str] = {}
        explicit = any(word in question for word in ("各", "每个", "按", "分别", "分组", "前", "top", "超过", "大于", "高于", "低于", "小于", "不超过", "至少", "排名", "占比", "倒数", "最高的", "最低的"))
        explicit = explicit or bool(lexicon.parse_ordinal_ranks(question))
        for link in links:
            if link.role != "dimension" or link.column == metric_column or link.column in seen:
                continue
            if link.table == table.name and any(
                candidate.table == table.name and candidate.column == link.column and candidate.role == "metric"
                for candidate in (all_links or ())
            ):
                continue
            is_date = self._is_date_column(link.column, self._schema_column_type(tables, link.table, link.column))
            if is_date and not any(word in question for word in _TREND_WORDS):
                continue
            if explicit or is_date:
                dimensions.append(link.column)
                dimension_tables[link.column] = link.table
                if is_date and any(word in question for word in ("按年", "每年")):
                    dimension_transforms[link.column] = "year"
                    dimension_labels[link.column] = "年份"
                elif is_date and any(word in question for word in ("按月", "每月", "趋势")):
                    dimension_transforms[link.column] = "month"
                    dimension_labels[link.column] = "月份"
                seen.add(link.column)
        return dimensions, dimension_tables, dimension_transforms, dimension_labels

    @staticmethod
    def _value_filters(question: str, links: list, tables: tuple[TableInfo, ...], connection: sqlite3.Connection) -> list[FilterSpec]:
        """无 ValueIndex 时的兼容路径（仅对已链接列查值）。"""

        result: list[FilterSpec] = []
        question_normalized = normalize_text(question)
        by_name = {table.name: table for table in tables}
        seen: set[tuple[str, str]] = set()
        for link in links:
            if link.role != "dimension":
                continue
            target_table = by_name.get(link.table)
            if target_table is None or (link.table, link.column) in seen:
                continue
            seen.add((link.table, link.column))
            column = next((item for item in target_table.columns if item.name == link.column), None)
            if column is None or SingleTablePlanner._is_date_column(column.name, column.data_type) or column.data_type.upper() not in {"TEXT", "CHAR", "VARCHAR", "DATE", "DATETIME"}:
                continue
            values = connection.execute(
                f"SELECT DISTINCT {_quote(column.name)} FROM {_quote(target_table.name)} WHERE {_quote(column.name)} IS NOT NULL LIMIT 5001"
            ).fetchall()
            candidates = [str(row[0]) for row in values if str(row[0]) and normalize_text(str(row[0])) in question_normalized]
            if candidates:
                value = max(candidates, key=len)
                result.append(FilterSpec(column.name, "=", value, value, f"识别到 {column.name}={value}", target_table.name))
        return result

    @staticmethod
    def _is_date_column(column: str, data_type: str = '') -> bool:
        return is_date_column(column, data_type)

    # ================================================================ JOIN graph
    def _adjacency(self, by_name: dict[str, TableInfo]) -> dict[str, list[tuple[str, str, str]]]:
        adjacency: dict[str, list[tuple[str, str, str]]] = {name: [] for name in by_name}
        for child in sorted(by_name.values(), key=lambda item: item.name):
            constraints = {}
            for i, fk in enumerate(child.foreign_keys):
                key = (fk.table, fk.constraint_id if fk.constraint_id is not None else f"single-{i}")
                constraints.setdefault(key, []).append(fk)
            for group in constraints.values():
                group = sorted(group, key=lambda fk: fk.sequence)
                fk = group[0]
                if fk.table not in by_name:
                    continue
                parent = by_name[fk.table]
                parent_keys = parent.unique_keys or (tuple(c.name for c in parent.columns if c.primary_key),)
                target_columns = {item.to_column for item in group}
                if not any(key and set(key) == target_columns for key in parent_keys):
                    continue  # declaration alone cannot prove many-to-one against non-unique targets
                condition = " AND ".join(f"{_quote(child.name)}.{_quote(item.from_column)} = {_quote(fk.table)}.{_quote(item.to_column)}" for item in group)
                adjacency[child.name].append((fk.table, condition, "many_to_one"))
                source_columns = {item.from_column for item in group}
                child_keys = child.unique_keys or (tuple(c.name for c in child.columns if c.primary_key),)
                reverse_cardinality = "many_to_one" if any(key and set(key) == source_columns for key in child_keys) else "one_to_many"
                adjacency[fk.table].append((child.name, condition, reverse_cardinality))
        for neighbors in adjacency.values():
            neighbors.sort(key=lambda item: (item[0], item[1]))
        return adjacency

    def _shortest_paths(self, adjacency, start: str, target: str, banned: frozenset[str] = frozenset()):
        queue: deque = deque([(start, [], frozenset({start}) | banned)])
        found_depth: int | None = None
        matches: list[list[tuple[str, str, str, str]]] = []
        while queue:
            current, path, visited = queue.popleft()
            if found_depth is not None and len(path) > found_depth:
                continue
            if current == target:
                found_depth = len(path) if found_depth is None else found_depth
                matches.append(path)
                continue
            if len(path) >= self.max_join_hops:
                continue
            for neighbor, condition, cardinality in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                queue.append((neighbor, [*path, (current, neighbor, condition, cardinality)], visited | {neighbor}))
        unique: dict[tuple[str, ...], list] = {}
        for path in matches:
            # 签名包含 JOIN 条件：同一对表之间多条外键（角色扮演维度）不再被合并
            signature = tuple([start, *[f"{item[1]}|{item[2]}" for item in path]])
            unique.setdefault(signature, path)
        return [unique[key] for key in sorted(unique)]

    def _resolve_joins(self, base_table, required_tables, tables, *, path_hint=None, via: dict[str, str] | None = None):
        if required_tables <= {base_table}:
            return [], [], [], []
        by_name = {table.name: table for table in tables}
        if base_table not in by_name or any(name not in by_name for name in required_tables):
            return None
        adjacency = self._adjacency(by_name)
        paths: dict[str, list] = {}
        alternatives: list[dict[str, str]] = []
        for target in sorted(required_tables - {base_table}):
            anchor = (via or {}).get(target)
            if anchor and anchor not in {base_table, target}:
                first = self._shortest_paths(adjacency, base_table, anchor)
                if not first:
                    return None
                second = self._shortest_paths(adjacency, anchor, target, banned=frozenset(step[0] for step in first[0]))
                if not second:
                    return None
                paths[target] = [*first[0], *second[0]]
                continue
            candidates = self._shortest_paths(adjacency, base_table, target)
            if not candidates:
                return None
            if len(candidates) > 1:
                selected = next((c for c in candidates if self._path_signature(c, base_table) == path_hint), None) if path_hint else None
                if selected is None:
                    signatures = [self._path_signature(c, base_table) for c in candidates]
                    if len(set(signatures)) < len(signatures):
                        # 表序列相同、外键不同：优先选择与指标表直接相连的第一条，并在假设中说明
                        candidates = [candidates[0]]
                    else:
                        alternatives.extend(
                            {"target": target, "signature": sig, "label": f"{target}: {sig.replace('>', ' → ')}"}
                            for sig in signatures
                        )
                        continue
                else:
                    candidates = [selected]
            paths[target] = candidates[0]
        if alternatives:
            return [], [], [], alternatives
        join_tables: list[str] = []
        join_conditions: list[str] = []
        join_path: list[dict[str, str]] = []
        joined = {base_table}
        remaining = set(paths)
        while remaining:
            ready = [(len(paths[t]), t, paths[t]) for t in remaining if paths[t] and paths[t][0][0] in joined]
            if not ready:
                return None
            _, target, path = min(ready, key=lambda item: (item[0], item[1]))
            for source, destination, condition, cardinality in path:
                if destination in joined:
                    continue
                if source not in joined:
                    return None
                join_tables.append(destination)
                join_conditions.append(condition)
                join_path.append({"from_table": source, "to_table": destination, "condition": condition, "cardinality": cardinality})
                joined.add(destination)
            remaining.remove(target)
        return join_tables, join_conditions, join_path, []

    @staticmethod
    def _path_signature(path, base_table: str) -> str:
        return ">".join([base_table, *[item[1] for item in path]])

    @staticmethod
    def _path_hint(question: str) -> str | None:
        match = _JOIN_HINT_RE.search(question or "")
        return match.group(1) if match else None

    @staticmethod
    def _limit(question: str) -> int:
        top = lexicon.parse_top_n(normalize_text(question))
        return max(1, min(_MAX_LIMIT, top.n)) if top else _MAX_LIMIT
