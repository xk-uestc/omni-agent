"""按原生事实粒度独立聚合的 NL2SQL 编译器。

共同维度要求 many-to-one 可达；子表筛选先生成主键集合，作为半连接。
多个指标在聚合后合并，避免 orders × items × refunds 的乘法放大。
公式只接受有界表达式树，值参数化，标识符只能来自 Schema。
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from .models import MetricSpec, QueryPlan, TableInfo


class MetricPlanError(ValueError):
    def __init__(self, message: str, code: str = "invalid_metric_plan"):
        super().__init__(message)
        self.code = code


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class MetricCompiler:
    """规则和模型使用同一编译器，输出单条只读参数化 WITH 查询。"""

    FUNCTIONS = {"SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"}
    OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "LIKE", "RANGE", "BETWEEN", "IN", "NOT IN"}

    def __init__(self, resolver):
        self.resolver = resolver

    def _joins(self, base, required, tables):
        resolved = self.resolver.resolve_joins(base, required | {base}, tables)
        if resolved is None:
            raise MetricPlanError("指标与维度/条件之间没有可验证的关联路径", "unreachable_join_path")
        if resolved[3]:
            raise MetricPlanError("存在多条业务关联路径，请先确认关系", "ambiguous_join_path")
        return resolved

    @staticmethod
    def _ref(table, column):
        return f"{quote(table)}.{quote(column)}"

    def _filters(self, filters, by_name):
        clauses, params = [], []
        for item in filters:
            if item.table not in by_name or item.column not in {c.name for c in by_name[item.table].columns}:
                raise MetricPlanError("过滤字段不存在")
            if item.operator not in self.OPERATORS:
                raise MetricPlanError("不支持的过滤运算符")
            target = self._ref(item.table, item.column)
            if item.operator in {"RANGE", "BETWEEN"}:
                if not isinstance(item.value, (tuple, list)) or len(item.value) != 2:
                    raise MetricPlanError("时间/数值区间必须有两个边界")
                clauses.append(f"{target} >= ? AND {target} {'<' if item.operator == 'RANGE' else '<='} ?")
                params.extend(item.value)
            elif item.operator in {"IN", "NOT IN"}:
                if not isinstance(item.value, (tuple, list)) or not 1 <= len(item.value) <= 50:
                    raise MetricPlanError("集合条件必须包含 1 到 50 项")
                clauses.append(f"{target} {item.operator} ({', '.join('?' for _ in item.value)})")
                params.extend(item.value)
            elif item.value is None:
                if item.operator not in {"=", "!="}:
                    raise MetricPlanError("NULL 仅允许相等/不等判断")
                clauses.append(f"{target} IS {'NOT ' if item.operator == '!=' else ''}NULL")
            else:
                clauses.append(f"{target} {item.operator} ?")
                params.append(item.value)
        if any(not isinstance(p, (str, int, float, type(None))) or isinstance(p, float) and not math.isfinite(p) for p in params):
            raise MetricPlanError("条件值必须是有限标量")
        return clauses, params

    @staticmethod
    def _from(base, joins, *, left):
        sql = f"FROM {quote(base)}"
        for table, condition in zip(joins[0], joins[1]):
            sql += f" {'LEFT JOIN' if left else 'JOIN'} {quote(table)} ON {condition}"
        return sql

    def compile(self, plan: QueryPlan, tables: tuple[TableInfo, ...]):
        if plan.clarification:
            raise MetricPlanError("待澄清计划不能执行")
        if plan.comparison_mode != "none":
            raise MetricPlanError("多指标周期比较尚需明确每个指标的时间角色", "unsupported_comparison_combination")
        metrics = plan.metrics or [MetricSpec("metric", plan.metric_table or plan.table,
                                             plan.metric_column, plan.metric_function, plan.metric_label or plan.metric_column)]
        if not 1 <= len(metrics) <= 8 or len(plan.derived_metrics) > 8 or len(plan.dimensions) > 8:
            raise MetricPlanError("指标/维度数量超过计划限制")
        by_name = {t.name: t for t in tables}
        ids = [m.id for m in metrics] + [m.id for m in plan.derived_metrics]
        labels = [m.label for m in metrics] + [m.label for m in plan.derived_metrics]
        dim_labels = [plan.dimension_labels.get(c, c) for c in plan.dimensions]
        if len(ids) != len(set(ids)) or len(labels + dim_labels) != len(set(labels + dim_labels)):
            raise MetricPlanError("指标 ID 或展示列名重复")
        if any(not isinstance(label, str) or not label or len(label) > 80 for label in labels + dim_labels):
            raise MetricPlanError("展示名称无效")
        if plan.analysis_mode not in {"aggregate", "rank", "share"}:
            raise MetricPlanError("不支持的分析模式")
        if plan.analysis_mode == "share" and len(metrics) + len(plan.derived_metrics) > 1 and plan.order_metric is None:
            raise MetricPlanError("多指标占比需要指定分母指标", "ambiguous_metric")
        keys = [f"__d{i}" for i in range(len(plan.dimensions))]
        ctes, params, audits, expressions, types = [], [], [], {}, {}
        for index, metric in enumerate(metrics):
            table = by_name.get(metric.table)
            if table is None or metric.column not in {c.name for c in table.columns}:
                raise MetricPlanError("指标字段不存在")
            column = next(c for c in table.columns if c.name == metric.column)
            if metric.function not in self.FUNCTIONS:
                raise MetricPlanError("不支持的聚合函数")
            if metric.function in {"SUM", "AVG", "MIN", "MAX"} and not any(k in column.data_type.upper() for k in ("INT", "REAL", "NUM", "DEC", "FLOAT", "DOUBLE")):
                raise MetricPlanError("数值聚合要求数值字段")
            if metric.missing not in {"null", "zero"}:
                raise MetricPlanError("缺失策略必须为 null 或 zero")
            if metric.missing == "zero" and metric.function not in {"SUM", "COUNT", "COUNT_DISTINCT"}:
                raise MetricPlanError("均值/极值缺失不能伪造为零")
            dimension_tables = {plan.dimension_tables.get(c, plan.table) for c in plan.dimensions}
            dimension_joins = self._joins(metric.table, dimension_tables, tables)
            dimension_fan_out = any(edge["cardinality"] == "one_to_many" for edge in dimension_joins[2])
            duplicate_insensitive = metric.function in {"COUNT_DISTINCT", "MIN", "MAX"}
            if dimension_fan_out and not duplicate_insensitive:
                raise MetricPlanError("分组维度会把一个事实分配到多个组，请确认分摊口径", "fan_out_risk")
            dimensions = []
            for i, c in enumerate(plan.dimensions):
                target_table = plan.dimension_tables.get(c, plan.table)
                if target_table not in by_name or c not in {col.name for col in by_name[target_table].columns}:
                    raise MetricPlanError("分组字段不存在")
                expr = self._ref(target_table, c)
                transform = plan.dimension_transforms.get(c, "raw")
                if transform in {"month", "year"}:
                    expr = f"strftime('{ '%Y-%m' if transform == 'month' else '%Y' }', {expr})"
                elif transform != "raw":
                    raise MetricPlanError("不支持的时间粒度")
                dimensions.append((expr, f"__d{i}"))
            filters = [replace(f, table=f.table or plan.table) for f in plan.filters] + [replace(f, table=f.table or metric.table) for f in metric.filters]
            filter_joins = self._joins(metric.table, {f.table for f in filters}, tables)
            clauses, filter_params = self._filters(filters, by_name)
            fan_out = any(edge["cardinality"] == "one_to_many" for edge in filter_joins[2])
            grain = [c.name for c in table.columns if c.primary_key]
            where = ""
            if dimension_fan_out:
                source_joins = self._joins(metric.table, dimension_tables | {f.table for f in filters}, tables)
                if clauses:
                    where = " WHERE " + " AND ".join(clauses)
            elif fan_out:
                if not grain:
                    raise MetricPlanError("子表筛选需要可验证的事实主键，当前表没有主键", "fan_out_risk")
                eligibility_name = f"__eligible{index}"
                selected = ", ".join(self._ref(metric.table, key) for key in grain)
                eligibility = f"SELECT DISTINCT {selected} {self._from(metric.table, filter_joins, left=False)}"
                if clauses:
                    eligibility += " WHERE " + " AND ".join(clauses)
                ctes.append(f"{quote(eligibility_name)} AS ({eligibility})")
                where = f" WHERE ({selected}) IN (SELECT {', '.join(quote(k) for k in grain)} FROM {quote(eligibility_name)})"
                source_joins = dimension_joins
            else:
                source_joins = self._joins(metric.table, dimension_tables | {f.table for f in filters}, tables)
                if any(edge["cardinality"] == "one_to_many" for edge in source_joins[2]):
                    raise MetricPlanError("合并关系出现未验证扇出", "fan_out_risk")
                if clauses:
                    where = " WHERE " + " AND ".join(clauses)
            params.extend(filter_params)
            target = self._ref(metric.table, metric.column)
            aggregate = "COUNT(*)" if metric.function == "COUNT" else f"COUNT(DISTINCT {target})" if metric.function == "COUNT_DISTINCT" else f"{metric.function}({target})"
            select = [f"{expr} AS {quote(key)}" for expr, key in dimensions] + [f"{aggregate} AS \"__value\""]
            sql = f"SELECT {', '.join(select)} {self._from(metric.table, source_joins, left=True)}{where}"
            if dimensions:
                sql += " GROUP BY " + ", ".join(expr for expr, _ in dimensions)
            ctes.append(f'"__m{index}" AS ({sql})')
            value = f'"__m{index}"."__value"'
            if metric.missing == "zero":
                value = f"COALESCE({value}, 0)"
            expressions[metric.id] = value
            types[metric.id] = (metric.unit, metric.currency)
            audits.append({"metric_id": metric.id, "table": metric.table, "column": metric.column,
                           "function": metric.function, "native_grain": grain, "unit": metric.unit,
                           "currency": metric.currency, "missing": metric.missing,
                           "filter_strategy": "duplicate_insensitive_aggregate" if dimension_fan_out else "primary_key_semijoin" if fan_out else "many_to_one",
                           "dimension_path": dimension_joins[2], "filter_path": filter_joins[2]})
        if keys:
            ctes.append('"__keys" AS (' + " UNION ".join(f"SELECT {', '.join(quote(k) for k in keys)} FROM \"__m{i}\"" for i in range(len(metrics))) + ")")
            from_sql = 'FROM "__keys"'
            for i in range(len(metrics)):
                on = " AND ".join(f'"__keys".{quote(k)} IS "__m{i}".{quote(k)}' for k in keys)
                from_sql += f' LEFT JOIN "__m{i}" ON {on}'
            select = [f'"__keys".{quote(key)} AS {quote(label)}' for key, label in zip(keys, dim_labels)]
        else:
            from_sql = 'FROM "__m0"' + "".join(f' CROSS JOIN "__m{i}"' for i in range(1, len(metrics)))
            select = []
        select.extend(f"{expressions[m.id]} AS {quote(m.label)}" for m in metrics)
        ctes.append(f'"__combined" AS (SELECT {", ".join(select)} {from_sql})')
        source = '"__combined"'
        expressions = {m.id: quote(m.label) for m in metrics}
        formula_audit = []
        for i, derived in enumerate(plan.derived_metrics):
            expression, unit = self._expression(derived.expression, expressions, types, params, [0], 0)
            ctes.append(f'"__calc{i}" AS (SELECT *, {expression} AS {quote(derived.label)} FROM {source})')
            source = f'"__calc{i}"'
            expressions[derived.id], types[derived.id] = quote(derived.label), unit
            formula_audit.append({"metric_id": derived.id, "expression": derived.expression,
                                  "unit": unit[0], "currency": unit[1], "zero_denominator": "null"})
        id_to_label = {m.id: m.label for m in [*metrics, *plan.derived_metrics]}
        outputs = plan.output_metrics or ids
        if len(outputs) != len(set(outputs)) or any(m not in id_to_label for m in outputs):
            raise MetricPlanError("输出指标不存在或重复")
        sort_id = plan.order_metric or outputs[0]
        if sort_id not in id_to_label:
            raise MetricPlanError("排序指标不存在")
        sort = quote(id_to_label[sort_id])
        direction = "DESC" if plan.order_desc else "ASC"
        visible = [quote(label) for label in [*dim_labels, *(id_to_label[m] for m in outputs)]]
        where = ""
        if plan.having:
            if plan.having.operator not in {">", ">=", "<", "<=", "=", "!="}:
                raise MetricPlanError("阈值运算符无效")
            if plan.having.mode in {"scalar", "literal"}:
                where = f" WHERE {sort} {plan.having.operator} ?"
                params.append(plan.having.value)
            elif plan.having.mode == "scalar_avg":
                where = f' WHERE {sort} {plan.having.operator} (SELECT AVG({sort}) FROM {source})'
            else:
                raise MetricPlanError("阈值模式无效")
        if plan.analysis_mode == "share":
            if not keys:
                raise MetricPlanError("占比需要分组维度", "missing_analysis_dimension")
            if "占比(%)" in labels + dim_labels:
                raise MetricPlanError("展示名称与占比列冲突")
            ctes.append(f'"__share" AS (SELECT *, 100.0 * {sort} / NULLIF(SUM({sort}) OVER (), 0) AS "占比(%)" FROM {source}{where})')
            source, where = '"__share"', ""
            visible.append('"占比(%)"')
        ranked = bool(plan.top_n or plan.analysis_mode == "rank")
        if ranked:
            if not keys:
                raise MetricPlanError("排名需要分组维度", "missing_analysis_dimension")
            if "排名" in labels + dim_labels:
                raise MetricPlanError("展示名称与排名列冲突")
            ctes.append(f'"__ranked" AS (SELECT *, DENSE_RANK() OVER (ORDER BY {sort} {direction}) AS "__rank" FROM {source}{where})')
            source = '"__ranked"'
            where = ""
            if plan.top_n is not None:
                if isinstance(plan.top_n, bool) or not isinstance(plan.top_n, int) or not 1 <= plan.top_n <= 100:
                    raise MetricPlanError("Top-N 必须在 1 到 100 之间")
                where = ' WHERE "__rank" <= ?'
                params.append(plan.top_n)
            if plan.analysis_mode == "rank":
                visible.append('"__rank" AS "排名"')
        if not isinstance(plan.limit, int) or isinstance(plan.limit, bool) or not 1 <= plan.limit <= 1000:
            raise MetricPlanError("结果上限无效")
        sql = "WITH " + ", ".join(ctes) + f" SELECT {', '.join(visible)} FROM {source}{where} ORDER BY {sort} {direction}"
        if dim_labels:
            sql += ", " + ", ".join(quote(label) for label in dim_labels)
        sql += " LIMIT ?"
        params.append(plan.limit)
        plan.grain_audit = {"strategy": "aggregate_each_fact_then_join", "metrics": audits,
                            "formulas": formula_audit, "null_group_alignment": "IS", "order_metric": sort_id}
        return sql, tuple(params)

    def _expression(self, node, expressions, types, params, count, depth):
        count[0] += 1
        if count[0] > 64 or depth > 8 or not isinstance(node, dict):
            raise MetricPlanError("公式表达式超出限制")
        if set(node) == {"ref"}:
            if node["ref"] not in expressions:
                raise MetricPlanError("公式引用了不存在或尚未定义的指标")
            return expressions[node["ref"]], types[node["ref"]]
        if set(node) == {"constant"}:
            value = node["constant"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e12:
                raise MetricPlanError("公式常量必须是有界有限数")
            params.append(value)
            return "?", ("1", None)
        if set(node) != {"op", "left", "right"} or node["op"] not in {"add", "subtract", "multiply", "divide"}:
            raise MetricPlanError("公式只允许有界四则运算表达式树")
        left, lt = self._expression(node["left"], expressions, types, params, count, depth + 1)
        right, rt = self._expression(node["right"], expressions, types, params, count, depth + 1)
        op = node["op"]
        if op in {"add", "subtract"}:
            if lt != rt or lt[0] == "unknown":
                raise MetricPlanError("加减法要求已知且一致的单位和币种", "metric_unit_mismatch")
            unit = lt
        elif op == "multiply":
            if lt[0] == "1":
                unit = rt
            elif rt[0] == "1":
                unit = lt
            else:
                raise MetricPlanError("乘法必须有一个无量纲参数", "metric_unit_mismatch")
        else:
            if lt[0] == "unknown" or rt[0] == "unknown" or lt[1] and rt[1] and lt[1] != rt[1]:
                raise MetricPlanError("除法单位/币种不能确定或冲突", "metric_unit_mismatch")
            unit = ("1", None) if lt == rt else (lt[0] if rt[0] == "1" else f"{lt[0]}/{rt[0]}", lt[1])
        symbol = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}[op]
        return (f"(1.0 * ({left}) / NULLIF(({right}), 0))" if op == "divide" else f"(({left}) {symbol} ({right}))"), unit
