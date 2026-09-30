"""实体值索引：让"华东""东辰科技""星河 Pro"这类取值直接定位到列。

原实现只在问题里出现列别名（如"地区"）时才去查值，导致只写取值、不写列名的
问题静默丢失过滤。本模块在 Schema 快照上一次性构建低基数文本列的取值索引：

- 精确匹配：归一化取值是问题的子串即命中，最长优先、互不重叠；
- 词头歧义：取值按空格/连字符切出的词头（"星河"）同时对应多个取值时，
  返回候选供澄清，而不是猜测；
- 同义词：可选 JSON 文件把业务别名（"帝都"->"北京"）映射到库内真实取值；
- 单字取值（如等级"高"）只有在其列被别名显式链接时才参与匹配，避免
  "高于平均"里的"高"被误识别。

索引构建只读、有界：跳过主键/外键/*_id/日期列，基数超过上限的列不入索引。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import TableInfo
from .schema import normalize_text

_MAX_DISTINCT = 5000
_TEXT_TYPES = ("TEXT", "CHAR", "CLOB", "VARCHAR", "STRING", "")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _is_date_name(name: str) -> bool:
    value = normalize_text(name)
    return value.endswith("date") or value.endswith("time") or value in {"日期", "时间"}


@dataclass(frozen=True)
class ValueEntry:
    table: str
    column: str
    value: str
    normalized: str


@dataclass(frozen=True)
class ValueMatch:
    table: str
    column: str
    value: str
    span: str
    start: int
    end: int
    negated: bool = False
    via: str = "exact"  # exact | synonym | head


@dataclass(frozen=True)
class ValueAmbiguity:
    span: str
    candidates: tuple[ValueEntry, ...]
    kind: str  # head | column


class ValueIndex:
    def __init__(self, entries: Iterable[ValueEntry], synonyms: dict[str, tuple[ValueEntry, ...]] | None = None,
                 skipped_columns: Iterable[tuple[str, str]] = ()):
        self.entries = tuple(entries)
        self.by_normalized: dict[str, list[ValueEntry]] = {}
        self.by_head: dict[str, list[ValueEntry]] = {}
        for entry in self.entries:
            self.by_normalized.setdefault(entry.normalized, []).append(entry)
            parts = [part for part in re.split(r"[\s\-_·/()（）]+", entry.value) if part]
            if len(parts) > 1:
                head = normalize_text(parts[0])
                if len(head) >= 2 and head != entry.normalized:
                    self.by_head.setdefault(head, []).append(entry)
        self.synonyms = dict(synonyms or {})
        self.skipped_columns = tuple(skipped_columns)

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, connection: sqlite3.Connection, tables: tuple[TableInfo, ...],
              synonyms_path: str | Path | None = None) -> "ValueIndex":
        entries: list[ValueEntry] = []
        skipped: list[tuple[str, str]] = []
        for table in tables:
            fk_columns = {item.from_column for item in table.foreign_keys}
            for column in table.columns:
                kind = column.data_type.upper()
                if column.primary_key or column.name in fk_columns or _is_date_name(column.name):
                    continue
                if normalize_text(column.name).endswith("id"):
                    continue
                if not any(token in kind for token in _TEXT_TYPES if token) and kind not in _TEXT_TYPES:
                    continue
                rows = connection.execute(
                    f"SELECT DISTINCT {_quote(column.name)} FROM {_quote(table.name)} "
                    f"WHERE {_quote(column.name)} IS NOT NULL LIMIT {_MAX_DISTINCT + 1}"
                ).fetchall()
                if len(rows) > _MAX_DISTINCT:
                    skipped.append((table.name, column.name))
                    continue
                for (raw,) in rows:
                    text = str(raw).strip()
                    normalized = normalize_text(text)
                    if normalized:
                        entries.append(ValueEntry(table.name, column.name, text, normalized))
        index = cls(entries, skipped_columns=skipped)
        if synonyms_path:
            index.synonyms = cls._load_synonyms(synonyms_path, index)
        return index

    @staticmethod
    def _load_synonyms(path: str | Path, index: "ValueIndex") -> dict[str, tuple[ValueEntry, ...]]:
        """格式：[{"table":..,"column":..,"value":"北京","aliases":["帝都","北京市"]}]。

        指向库中不存在的取值会被忽略（部署前可用标注验收脚本报告）。
        """

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result: dict[str, list[ValueEntry]] = {}
        for item in payload if isinstance(payload, list) else payload.get("values", []):
            target = normalize_text(str(item.get("value", "")))
            entry = next(
                (e for e in index.by_normalized.get(target, []) if e.table == item.get("table") and e.column == item.get("column")),
                None,
            )
            if entry is None:
                continue
            for alias in item.get("aliases", []):
                key = normalize_text(str(alias))
                if key and key != entry.normalized:
                    result.setdefault(key, []).append(entry)
        return {key: tuple(value) for key, value in result.items()}

    # ------------------------------------------------------------------ match
    def match(self, normalized: str, linked_columns: set[tuple[str, str]],
              blocked: list[tuple[int, int]] | None = None) -> tuple[list[ValueMatch], list[ValueAmbiguity]]:
        occupied: list[tuple[int, int]] = list(blocked or [])

        def free(start: int, end: int) -> bool:
            return all(end <= a or start >= b for a, b in occupied)

        candidates: list[tuple[int, int, str, list[ValueEntry], str]] = []
        for key, entries in self.by_normalized.items():
            usable = [e for e in entries if len(key) >= 2 or (e.table, e.column) in linked_columns]
            if usable:
                candidates.extend((m.start(), m.end(), key, usable, "exact") for m in re.finditer(re.escape(key), normalized))
        for key, entries in self.synonyms.items():
            candidates.extend((m.start(), m.end(), key, list(entries), "synonym") for m in re.finditer(re.escape(key), normalized))
        # 最长优先，其次靠前
        candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
        matches: list[ValueMatch] = []
        ambiguities: list[ValueAmbiguity] = []
        for start, end, key, entries, via in candidates:
            if not free(start, end):
                continue
            columns = {(e.table, e.column) for e in entries}
            if len(columns) > 1:
                linked = [e for e in entries if (e.table, e.column) in linked_columns]
                if len({(e.table, e.column) for e in linked}) == 1:
                    entries = linked
                else:
                    ambiguities.append(ValueAmbiguity(normalized[start:end], tuple(entries), "column"))
                    occupied.append((start, end))
                    continue
            entry = entries[0]
            matches.append(ValueMatch(entry.table, entry.column, entry.value, normalized[start:end], start, end, via=via))
            occupied.append((start, end))
        # 词头：只在没有被精确匹配覆盖的位置检查
        for head, entries in self.by_head.items():
            for m in re.finditer(re.escape(head), normalized):
                if not free(m.start(), m.end()):
                    continue
                if len(entries) == 1:
                    entry = entries[0]
                    matches.append(ValueMatch(entry.table, entry.column, entry.value, head, m.start(), m.end(), via="head"))
                else:
                    ambiguities.append(ValueAmbiguity(head, tuple(entries), "head"))
                occupied.append((m.start(), m.end()))
        matches.sort(key=lambda item: item.start)
        return matches, ambiguities
