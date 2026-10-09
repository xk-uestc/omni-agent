"""SQLite Schema introspection 和泛化字段链接。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .t2s import to_simplified
from .models import ColumnInfo, ForeignKeyInfo, LinkCandidate, TableInfo
from .schema_profile import infer_rules
from .date_semantics import GENERIC_TIME_ALIASES, is_date_column
from .question_roles import (association_scope, field_owners, filter_only, group_fields,
                             record_count_subject, alias_owner_prefix, _group_spans,
                             excluded_filter_scope)


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]")


def normalize_text(value: str) -> str:
    """统一全角空格、大小写和常见标点，保留中文可检索性。"""

    # 繁→简字符归一：问题与库内取值两侧都经过这里，保证一致
    return to_simplified(re.sub(r"\s+", "", unicodedata.normalize('NFKC', value or '')).lower().replace("，", ","))


@dataclass(frozen=True)
class AliasRule:
    table: str
    column: str
    aliases: tuple[str, ...]
    role: str
    metric_function: str | None = None
    confidence: float | None = None


DEFAULT_ALIAS_RULES: tuple[AliasRule, ...] = (
    AliasRule('suppliers','supplier_name',('供应商','供应商名称'),'dimension'),
    AliasRule('suppliers','supplier_category',('供应商类型',),'dimension'),
    AliasRule('suppliers','supplier_rating',('供应商评分','平均供应商评分'),'metric','AVG'),
    AliasRule('purchase_orders','purchase_amount',('采购金额','采购额'),'metric','SUM'),
    AliasRule('purchase_orders','purchase_quantity',('采购数量','采购件数'),'metric','SUM'),
    AliasRule('purchase_orders','purchase_id',('采购单数',),'metric','COUNT'),
    AliasRule('purchase_orders','lead_time_days',('平均采购周期','采购交期'),'metric','AVG'),
    AliasRule('purchase_orders','purchase_status',('采购状态',),'dimension'),
    AliasRule('shipment_records','shipping_cost',('运费','物流费用','运输费用'),'metric','SUM'),
    AliasRule('shipment_records','shipment_id',('发货单数','物流单数'),'metric','COUNT'),
    AliasRule('shipment_records','transit_days',('平均运输天数','平均配送时长'),'metric','AVG'),
    AliasRule('shipment_records','carrier',('承运商','物流公司'),'dimension'),
    AliasRule('shipment_records','shipment_status',('物流状态','配送状态'),'dimension'),
    AliasRule('payment_receipts','collected_amount',('回款金额','实收金额','已收款金额'),'metric','SUM'),
    AliasRule('payment_receipts','receipt_id',('回款笔数',),'metric','COUNT'),
    AliasRule('payment_receipts','receipt_status',('回款状态',),'dimension'),
    AliasRule('payment_receipts','payment_method',('支付方式','回款方式'),'dimension'),
    AliasRule('operating_expenses','expense_amount',('费用金额','运营费用','报销金额'),'metric','SUM'),
    AliasRule('operating_expenses','expense_category',('费用类别','费用类型'),'dimension'),
    AliasRule('operating_expenses','approval_status',('审批状态',),'dimension'),
    AliasRule('employees','employee_id',('员工数','员工人数'),'metric','COUNT'),
    AliasRule('employees','employee_name',('员工姓名',),'dimension'),
    AliasRule('employees','employment_type',('用工类型',),'dimension'),
    AliasRule('employees','employee_status',('员工状态','在职状态'),'dimension'),
    AliasRule('payroll_records','salary_amount',('薪酬总额','工资总额'),'metric','SUM'),
    AliasRule('payroll_records','bonus_amount',('奖金总额','奖金金额'),'metric','SUM'),
    AliasRule('payroll_records','overtime_hours',('加班时长','加班小时数'),'metric','SUM'),
    AliasRule('web_traffic_daily','page_views',('网页浏览量','页面浏览量'),'metric','SUM'),
    AliasRule('web_traffic_daily','visits',('网站访问量','访问次数'),'metric','SUM'),
    AliasRule('web_traffic_daily','web_conversions',('网站转化数',),'metric','SUM'),
    AliasRule('web_traffic_daily','traffic_source',('流量来源',),'dimension'),
    AliasRule('web_traffic_daily','device_type',('设备类型',),'dimension'),
    AliasRule(
        "sales_orders",
        "sales_amount",
        ("销售额", "销售金额", "营业额", "成交金额", "卖了多少钱", "卖出多少钱", "卖了多少金额"),
        "metric",
    ),
    AliasRule("sales_orders", "quantity", ("销量", "销售数量", "数量", "件数"), "metric"),
    # Demo contract: mean of the per-order unit price, not their sum or a
    # quantity-weighted price. Other schemas retain their own explicit rules.
    AliasRule("sales_orders", "unit_price", ("平均单价", "单价", "平均价格", "价格"), "metric", metric_function="AVG"),
    AliasRule("sales_orders", "order_id", ("订单数", "订单数量", "订单", "笔数"), "metric"),
    AliasRule("sales_orders", "customer_id", ("客户数", "客户数量", "不同客户数", "去重客户"), "metric"),
    AliasRule("sales_orders", "region", ("地区", "区域", "省份", "地域"), "dimension"),
    AliasRule("sales_orders", "channel", ("渠道", "销售渠道"), "dimension"),
    AliasRule("sales_orders", "product_category", ("品类", "产品类别", "商品类别", "类别"), "dimension"),
    AliasRule("sales_orders", "product_name", ("产品", "商品", "产品名称", "商品名称"), "dimension"),
    AliasRule("sales_orders", "order_date", ("日期", "时间", "下单日期", "销售日期", "月份", "月度", "按月", "年份", "按年"), "dimension"),
    AliasRule("customers", "customer_name", ("客户", "客户名称", "顾客", "顾客名称"), "dimension"),
    AliasRule("customers", "customer_level", ("客户等级", "会员等级", "等级"), "dimension"),
    AliasRule("customers", "industry", ("行业", "客户行业"), "dimension"),
)


class SchemaIntrospector:
    """从真实 SQLite 元数据构造稳定、可解释的 Schema 快照。"""

    def introspect(self, connection: sqlite3.Connection, *, include_row_count: bool = True) -> tuple[TableInfo, ...]:
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_traits = {str(item[1]): (str(item[2]), bool(item[4]))
                        for item in connection.execute('PRAGMA main.table_list').fetchall()}
        result: list[TableInfo] = []
        for row in tables:
            name = str(row[0])
            columns = connection.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")').fetchall()
            foreign_keys = connection.execute(
                f'PRAGMA foreign_key_list("{name.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            unique_keys = [tuple(str(c[1]) for c in sorted(columns, key=lambda c: c[5]) if c[5])]
            indexes = connection.execute(f'PRAGMA index_list("{name.replace(chr(34), chr(34) * 2)}")').fetchall()
            primary = [item for item in columns if item[5]]
            # Exact INTEGER and a single PK on an ordinary rowid table are
            # necessary; no PK-origin index proves this is the rowid alias.
            # INT/TEXT, composite PK, and INTEGER PRIMARY KEY DESC can remain
            # nullable in SQLite. Never infer all primary keys are non-null.
            rowid_alias = (str(primary[0][1]) if len(primary) == 1
                           and str(primary[0][2]).upper().strip() == 'INTEGER'
                           and table_traits.get(name) == ('table', False)
                           and not any(index[3] == 'pk' for index in indexes) else None)
            for index in indexes:
                if not index[2] or index[4]:  # partial indexes do not establish unconditional cardinality
                    continue
                index_name = str(index[1]).replace('"', '""')
                index_columns = connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
                if index_columns and all(c[2] is not None for c in index_columns):
                    unique_keys.append(tuple(str(c[2]) for c in index_columns))
            implicit_targets = {}
            for item in foreign_keys:
                if item[4] is None and item[2] not in implicit_targets:
                    parent_name = str(item[2]).replace('"', '""')
                    parent_columns = connection.execute(f'PRAGMA table_info("{parent_name}")').fetchall()
                    implicit_targets[item[2]] = [str(c[1]) for c in sorted(parent_columns, key=lambda c: c[5]) if c[5]]
            # 行数只在 /schema 展示时需要；问数热路径跳过全表 COUNT(*)
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0] if include_row_count else None
            result.append(
                TableInfo(
                    name=name,
                    columns=tuple(
                        ColumnInfo(
                            name=str(item[1]),
                            data_type=str(item[2] or "TEXT"),
                            nullable=not bool(item[3]) and str(item[1]) != rowid_alias,
                            primary_key=bool(item[5]),
                        )
                        for item in columns
                    ),
                    row_count=int(count) if count is not None else None,
                    foreign_keys=tuple(
                        ForeignKeyInfo(
                            table=str(item[2]),
                            from_column=str(item[3]),
                            to_column=str(item[4]) if item[4] is not None else implicit_targets[item[2]][item[1]],
                            constraint_id=int(item[0]),
                            sequence=int(item[1]),
                        )
                        for item in foreign_keys
                    ),
                    unique_keys=tuple(sorted({key for key in unique_keys if key})),
                )
            )
        return tuple(result)


class SchemaLinker:
    """基于 Schema 和业务别名的轻量字段链接器。

    链接器只负责候选和置信度，不直接拼接 SQL；这样无论后续接规则规划器还是
    LLM 规划器，所有执行都必须经过同一个安全层。
    """

    def __init__(self, rules: Iterable[AliasRule] = DEFAULT_ALIAS_RULES, *, infer_entity_counts: bool = True):
        self._rules = tuple(rules)
        self.infer_entity_counts = bool(infer_entity_counts)
        self._catalog_rules = ()
        self._rules_cache = OrderedDict()
        self._rules_lock = threading.RLock()
        # Explicit business annotations can identify an opaque physical name
        # as temporal; stored values are still verified by the date profiler.
        self._annotated_dates = frozenset((rule.table, rule.column) for rule in self._rules
            if rule.role == 'dimension' and any(is_date_column(alias)
                or normalize_text(alias) in GENERIC_TIME_ALIASES for alias in rule.aliases))

    def is_date_field(self, table: str, column: str, data_type: str = '') -> bool:
        return is_date_column(column, data_type) or (table, column) in self._annotated_dates

    def set_metric_catalog(self, catalog):
        """Add only otherwise unknown source aliases; existing rules win."""
        with self._rules_lock:
            self._catalog_rules = tuple(AliasRule(metric.table, metric.column,
                tuple(dict.fromkeys((metric.label, *catalog.aliases[metric_id],
                                     catalog.query_aliases.get(metric_id, metric.label)))),
                'metric', metric.function, 0.85)
                for metric_id, metric in catalog.sources.items()) if catalog else ()
            self._rules_cache.clear()

    def _effective_rules(self, tables: tuple[TableInfo, ...], *, include_profile_counts: bool = True) -> tuple[AliasRule, ...]:
        """将显式词典与运行时 Schema 画像合并，显式标注优先。"""
        key = (id(tables), include_profile_counts)
        with self._rules_lock:
            cached = self._rules_cache.get(key)
            if cached is not None and cached[0] is tables:
                self._rules_cache.move_to_end(key)
                return cached[1]
        pairs = {(rule.table, rule.column) for rule in self._rules}
        generated = (
            AliasRule(item.table, item.column, item.aliases, item.role, item.metric_function, item.confidence)
            for item in infer_rules(tables, infer_entity_counts=include_profile_counts and self.infer_entity_counts)
            if (item.table, item.column) not in pairs
        )
        base = (*self._rules, *generated)
        known = {normalize_text(alias) for rule in base for alias in rule.aliases}
        available = {(table.name, column.name) for table in tables for column in table.columns}
        supplements = tuple(AliasRule(rule.table, rule.column,
            tuple(alias for alias in rule.aliases if normalize_text(alias) not in known),
            rule.role, rule.metric_function, rule.confidence)
            for rule in self._catalog_rules if (rule.table, rule.column) in available
            and any(normalize_text(alias) not in known for alias in rule.aliases))
        result = (*base, *supplements)
        with self._rules_lock:
            self._rules_cache[key] = (tables, result)
            while len(self._rules_cache) > 8:
                self._rules_cache.popitem(last=False)
        return result

    def rules_for(self, tables: Iterable[TableInfo]) -> tuple[AliasRule, ...]:
        return self._effective_rules(tuple(tables))

    @classmethod
    def from_json(cls, path: str | Path) -> "SchemaLinker":
        """加载业务别名配置，不需要修改代码即可迁移到新 Schema。"""

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rules", [])
        functions = {"SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"}
        if not isinstance(payload, list):
            raise ValueError("Schema 别名配置必须是数组")
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"Schema 别名配置第 {index} 项必须是对象")
            table = item.get("table")
            column = item.get("column")
            role = str(item.get("role", "dimension")).strip().lower()
            aliases = item.get("aliases")
            if not isinstance(table, str) or not table.strip() or any(ord(ch) < 32 for ch in table):
                raise ValueError(f"Schema 别名配置第 {index} 项 table 非法")
            if not isinstance(column, str) or not column.strip() or any(ord(ch) < 32 for ch in column):
                raise ValueError(f"Schema 别名配置第 {index} 项 column 非法")
            if role not in {"metric", "dimension"}:
                raise ValueError(f"Schema 别名配置第 {index} 项 role 非法")
            if not isinstance(aliases, list) or not aliases or not all(
                isinstance(alias, str) and alias.strip() and not any(ord(ch) < 32 for ch in alias)
                for alias in aliases
            ):
                raise ValueError(f"Schema 别名配置第 {index} 项 aliases 必须是非空字符串数组")
            pair = (table, column)
            if pair in seen:
                raise ValueError(f"Schema 别名配置重复字段: {table}.{column}")
            seen.add(pair)
            function = str(item.get("metric_function", "")).upper().strip()
            if function and (role != "metric" or function not in functions):
                raise ValueError("metric_function 仅允许用于指标列，且必须是受支持的聚合函数")
        return cls(
            (
            AliasRule(
                table=item["table"].strip(),
                column=item["column"].strip(),
                aliases=tuple(alias.strip() for alias in item["aliases"]),
                role=str(item.get("role", "dimension")).strip().lower(),
                metric_function=str(item["metric_function"]).upper().strip() if item.get("metric_function") else None,
                )
                for item in payload
            ),
            infer_entity_counts=False,
        )

    def link(self, question: str, tables: Iterable[TableInfo]) -> list[LinkCandidate]:
        tables = tuple(tables)
        normalized, _, _ = association_scope(question, tables)
        normalized, _ = excluded_filter_scope(normalized, tables)
        ownership = field_owners(normalized, tables)
        table_names = {table.name for table in tables}
        rules = self._effective_rules(tables)
        explicit_groups = group_fields(normalized, tables, rules)
        group_spans = _group_spans(normalized, rules)

        def is_grouped_mention(start, end):
            return any(a <= start and end <= b for a, b in group_spans)

        links: list[LinkCandidate] = []
        linked_pairs: set[tuple[str, str]] = set()
        prefix_cache = {}
        alias_tables = {}
        for rule in rules:
            if rule.table in table_names:
                for alias in rule.aliases:
                    alias_tables.setdefault(normalize_text(alias), set()).add(rule.table)
        for rule in rules:
            if rule.table not in table_names:
                continue
            matches = [alias for alias in rule.aliases if normalize_text(alias) in normalized]
            matches = [alias for alias in matches if len(normalize_text(alias)) > 1 or
                       re.search(r'(?:^|按|各|每个|统计|的)' + re.escape(normalize_text(alias)) + r'(?:$|分组|统计|汇总)', normalized)]
            if not matches:
                continue
            alias = max(matches, key=len)
            score = (
                min(0.99, 0.58 + len(normalize_text(alias)) * 0.08)
                if rule.confidence is None
                else min(0.99, rule.confidence + min(0.2, len(normalize_text(alias)) * 0.02))
            )
            for mention in re.finditer(re.escape(normalize_text(alias)), normalized):
                prefix_key = (mention.start(), normalize_text(alias))
                if prefix_key not in prefix_cache:
                    prefix_cache[prefix_key] = alias_owner_prefix(
                        normalized, mention.start(), tables, alias_tables[normalize_text(alias)])
                prefix_start, owners = prefix_cache[prefix_key]
                if len(owners) == 1 and rule.table not in owners:
                    continue
                role = ('dimension' if is_grouped_mention(mention.start(), mention.end())
                        else rule.role)
                matched = normalized[prefix_start:mention.end()] if len(owners) == 1 else alias
                links.append(LinkCandidate(alias, rule.table, rule.column, role,
                                           round(score, 3), matched, rule.metric_function))
                linked_pairs.add((rule.table, rule.column))
        links.extend(self._fuzzy_links(normalized, table_names, links, rules))
        # 没有业务词典时仍按真实字段名链接，数值列默认作为指标，其他列作为维度。
        for table in tables:
            for column in table.columns:
                pair = (table.name, column.name)
                if pair in linked_pairs or len(column.name) < 2:
                    continue
                column_name = normalize_text(column.name)
                if not column_name or column_name not in normalized:
                    continue
                data_type = column.data_type.upper()
                role = "metric" if not is_date_column(column.name, data_type) and any(kind in data_type for kind in ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT")) else "dimension"
                links.append(
                    LinkCandidate(
                        source_text=column.name,
                        table=table.name,
                        column=column.name,
                        role=role,
                        score=0.7,
                        matched_alias=column.name,
                    )
                )
        if any(word in normalized for word in ('按月', '按年', '每月', '每年')):
            metric_tables = {link.table for link in links if link.role == 'metric'}
            column_types = {(table.name, column.name): column.data_type for table in tables for column in table.columns}
            explicit_date_role = any(link.role == 'dimension'
                and is_date_column(link.column, column_types.get((link.table, link.column), ''))
                and normalize_text(link.matched_alias) not in GENERIC_TIME_ALIASES for link in links)
            if len(metric_tables) == 1 and not explicit_date_role:
                table = next(table for table in tables if table.name in metric_tables)
                dates = [column for column in table.columns if is_date_column(column.name, column.data_type)]
                if len(dates) == 1 and not any(link.table == table.name and link.column == dates[0].name for link in links):
                    cue = next(word for word in ('按月', '按年', '每月', '每年') if word in normalized)
                    links.append(LinkCandidate(cue, table.name, dates[0].name, 'dimension', 0.8, cue))
        # Explicit Schema identifiers take precedence over substrings in other fields.
        protected = []
        for table in tables:
            for column in table.columns:
                column_text = normalize_text(column.name)
                # Bind an explicitly named owner before resolving homonymous
                # columns. Natural Chinese qualification (Table的Column) is
                # just as explicit as Table.Column; table names are literals,
                # never regex, and cannot match inside a longer SQL identifier.
                table_text = re.escape(normalize_text(table.name))
                qualified = (r'(?<![a-z0-9_])' + table_text
                             + r'(?:\.|表的|中的|内的|里的|的|表中|中)?'
                             + re.escape(column_text) + r'(?![a-z0-9_])')
                for match in re.finditer(qualified, normalized):
                    protected.append((match.start(), match.end(), table.name, column.name, True))
                if len(column_text) >= 3:
                    for match in re.finditer(re.escape(column_text), normalized):
                        protected.append((match.start(), match.end(), table.name, column.name, False))
        def shadowed(link):
            alias = normalize_text(link.matched_alias)
            positions = list(re.finditer(re.escape(alias), normalized))
            return bool(positions) and all(any(
                start <= match.start() and match.end() <= end
                and (table, column) != (link.table, link.column)
                and (qualified or end-start > len(alias))
                for start, end, table, column, qualified in protected
            ) for match in positions)
        explicit_concepts = {}
        for rule in rules:
            native = normalize_text(rule.column)
            native_positions = list(re.finditer(re.escape(native), normalized))
            standalone = any(not any(start <= match.start() and match.end() <= end and end-start > len(native)
                                     for start, end, _, _, _ in protected) for match in native_positions)
            if len(native) >= 3 and (standalone or ownership.get(rule.column) == {rule.table}):
                for alias in rule.aliases:
                    if rule.column not in ownership or rule.table in ownership[rule.column]:
                        explicit_concepts.setdefault((rule.role, normalize_text(alias)), set()).add((rule.table, rule.column))
        def disfavored(link):
            choices = explicit_concepts.get((link.role, normalize_text(link.matched_alias)), set())
            return len(choices) == 1 and (link.table, link.column) not in choices
        def dominated(link, other):
            small, large = normalize_text(link.matched_alias), normalize_text(other.matched_alias)
            if (other is link or other.role != link.role
                    or len(large) <= len(small) or small not in large):
                return False
            small_mentions = list(re.finditer(re.escape(small), normalized))
            larger_spans = [match.span() for match in re.finditer(re.escape(large), normalized)]
            if link.role == "dimension":
                grouped_mentions = [match for match in small_mentions
                                    if is_grouped_mention(match.start(), match.end())]
                # A generic suffix such as "状态" can fail the local grouping
                # cue check even though its only occurrence belongs to an
                # explicitly named compound dimension such as "采购状态".
                # Treat those fully covered occurrences as part of the longer
                # concept instead of reopening ambiguity across unrelated tables.
                if not grouped_mentions and all(any(
                        start <= match.start() and match.end() <= end
                        for start, end in larger_spans) for match in small_mentions):
                    return True
                small_mentions = grouped_mentions
                if not small_mentions:
                    return False
            return all(any(start <= match.start() and match.end() <= end for start, end in larger_spans)
                       for match in small_mentions)
        filtered_links = [
            link
            for link in links
            if not shadowed(link) and not disfavored(link) and not any(
                dominated(link, other)
                for other in links
            )
        ]
        # Actual physical mentions outrank generic aliases. A filter field is
        # not a requested measure; ownership cannot be inferred from confidence.
        filtered_links = [link for link in filtered_links if link.column not in ownership
                          or link.table in ownership[link.column]]
        for column, owners in ownership.items():
            for owner in owners:
                info = next(c for t in tables if t.name == owner for c in t.columns if c.name == column)
                rule = next((r for r in rules if (r.table, r.column) == (owner, column)), None)
                role = rule.role if rule else ('metric' if not is_date_column(column, info.data_type)
                    and any(kind in info.data_type.upper() for kind in ('INT', 'REAL', 'NUM', 'DEC', 'DOUBLE', 'FLOAT')) else 'dimension')
                if (owner, column) in explicit_groups:
                    role = 'dimension'
                if filter_only(normalized, column):
                    role = 'filter'
                filtered_links = [x for x in filtered_links if (x.table, x.column) != (owner, column)]
                filtered_links.append(LinkCandidate(column, owner, column, role, .99, column,
                    rule.metric_function if rule else None))
        count_subject = record_count_subject(normalized, tables)
        if count_subject:
            subject = next(t for t in tables if t.name == count_subject)
            keys = [c for c in subject.columns if c.primary_key]
            if len(keys) == 1 and not any(x.role == 'metric' and x.table == count_subject for x in filtered_links):
                cue = next(m.group() for m in re.finditer(r'记录数|记录计数|共有多少条|一共有多少条|有多少条|几条记录', normalized))
                filtered_links.append(LinkCandidate(cue, count_subject, keys[0].name, 'metric', .98, cue, 'COUNT'))
        metric_tables = {link.table for link in filtered_links if link.role == 'metric'}
        if len(metric_tables) == 1:
            # A shared department field is scoped by an explicit fact metric.
            # Keep unrelated/qualified dimensions and same-table ambiguity intact.
            native = [link for link in filtered_links if link.role == 'dimension'
                      and link.table in metric_tables and link.column == 'department']
            if len(native) == 1:
                filtered_links = [link for link in filtered_links if link.role != 'dimension'
                    or link.column != 'department' or link.matched_alias != native[0].matched_alias
                    or link.table in metric_tables]
        return sorted(filtered_links, key=lambda item: (-item.score, item.column))

    def _fuzzy_links(self, normalized: str, table_names: set[str], exact: list[LinkCandidate], rules: tuple[AliasRule, ...] | None = None) -> list[LinkCandidate]:
        """错别字容错：长度≥3 的别名允许 1 个汉字替换（拼音输入法同音误选最常见）。

        只在没有任何精确指标命中时启用；同一位置命中多个不同列时放弃（交给澄清）；
        命中结果的 matched_alias 保留问题原文，规划器会把"近似识别"写入假设。
        """

        if any(link.role == "metric" for link in exact):
            return []
        # 领域词表中的双字词：替换后若构成已知业务词（如"工单"），那是另一个词，不是错别字
        active_rules = rules or self._rules
        vocabulary = {normalize_text(alias) for rule in active_rules if rule.table in table_names for alias in rule.aliases}
        found: dict[tuple[int, int], set[tuple[str, str, str, str]]] = {}
        for rule in active_rules:
            if rule.table not in table_names:
                continue
            for alias in rule.aliases:
                target = normalize_text(alias)
                size = len(target)
                if size < 3:
                    continue
                for start in range(0, len(normalized) - size + 1):
                    window = normalized[start:start + size]
                    diffs = [i for i in range(size) if window[i] != target[i]]
                    # 汉语复合词中心语在后：末字不同（销售额/销售员/销售率）是另一个词，不是错别字
                    if len(diffs) == 1 and diffs[0] != size - 1 and "\u3400" <= window[diffs[0]] <= "\u9fff":
                        d = diffs[0]
                        bigrams = {window[max(0, d - 1):d + 1], window[d:d + 2]}
                        if any(len(b) == 2 and any(b in word for word in vocabulary) for b in bigrams):
                            continue
                        found.setdefault((start, start + size), set()).add((rule.table, rule.column, rule.role, alias))
        result: list[LinkCandidate] = []
        for (start, end), candidates in sorted(found.items()):
            columns = {(table, column) for table, column, _, _ in candidates}
            if len(columns) != 1:
                continue
            table, column, role, alias = sorted(candidates)[0]
            result.append(LinkCandidate(source_text=alias, table=table, column=column, role=role, score=0.55,
                                        matched_alias=normalized[start:end]))
        return result

    def rule_for(self, table: str, column: str) -> AliasRule | None:
        return next((rule for rule in self._rules if rule.table == table and rule.column == column), None)

    def dimension_columns(self, table: str) -> tuple[str, ...]:
        return tuple(rule.column for rule in self._rules if rule.table == table and rule.role == "dimension")
