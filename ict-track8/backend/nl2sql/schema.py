"""SQLite Schema introspection 和泛化字段链接。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .t2s import to_simplified
from .models import ColumnInfo, ForeignKeyInfo, LinkCandidate, TableInfo
from .schema_profile import infer_rules


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
        result: list[TableInfo] = []
        for row in tables:
            name = str(row[0])
            columns = connection.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")').fetchall()
            foreign_keys = connection.execute(
                f'PRAGMA foreign_key_list("{name.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            unique_keys = [tuple(str(c[1]) for c in sorted(columns, key=lambda c: c[5]) if c[5])]
            for index in connection.execute(f'PRAGMA index_list("{name.replace(chr(34), chr(34) * 2)}")').fetchall():
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
                            nullable=not bool(item[3]),
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

    def _effective_rules(self, tables: tuple[TableInfo, ...], *, include_profile_counts: bool = True) -> tuple[AliasRule, ...]:
        """将显式词典与运行时 Schema 画像合并，显式标注优先。"""

        pairs = {(rule.table, rule.column) for rule in self._rules}
        generated = (
            AliasRule(item.table, item.column, item.aliases, item.role, item.metric_function, item.confidence)
            for item in infer_rules(tables, infer_entity_counts=include_profile_counts and self.infer_entity_counts)
            if (item.table, item.column) not in pairs
        )
        return (*self._rules, *generated)

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
        normalized = normalize_text(question)
        tables = tuple(tables)
        table_names = {table.name for table in tables}
        rules = self._effective_rules(tables)
        links: list[LinkCandidate] = []
        linked_pairs: set[tuple[str, str]] = set()
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
            links.append(
                LinkCandidate(
                    source_text=alias,
                    table=rule.table,
                    column=rule.column,
                    role=rule.role,
                score=round(score, 3),
                matched_alias=alias,
                metric_function=rule.metric_function,
                )
            )
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
                role = "metric" if any(kind in data_type for kind in ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT")) else "dimension"
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
            if len(metric_tables) == 1:
                table = next(table for table in tables if table.name in metric_tables)
                dates = [column for column in table.columns if normalize_text(column.name).endswith(('date', 'time'))]
                if len(dates) == 1 and not any(link.table == table.name and link.column == dates[0].name for link in links):
                    cue = next(word for word in ('按月', '按年', '每月', '每年') if word in normalized)
                    links.append(LinkCandidate(cue, table.name, dates[0].name, 'dimension', 0.8, cue))
        # Explicit Schema identifiers take precedence over substrings in other fields.
        protected = []
        for table in tables:
            for column in table.columns:
                column_text = normalize_text(column.name)
                qualified = normalize_text(table.name) + r'\.?' + re.escape(column_text)
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
            if len(native) >= 3 and standalone:
                for alias in rule.aliases:
                    explicit_concepts.setdefault((rule.role, normalize_text(alias)), set()).add((rule.table, rule.column))
        def disfavored(link):
            choices = explicit_concepts.get((link.role, normalize_text(link.matched_alias)), set())
            return len(choices) == 1 and (link.table, link.column) not in choices
        filtered_links = [
            link
            for link in links
            if not shadowed(link) and not disfavored(link) and not any(
                other is not link
                and other.table == link.table
                and other.role == link.role
                and len(normalize_text(other.matched_alias)) > len(normalize_text(link.matched_alias))
                and normalize_text(link.matched_alias) in normalize_text(other.matched_alias)
                for other in links
            )
        ]
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
