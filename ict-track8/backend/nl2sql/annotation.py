"""业务 Schema 标注文件的迁移验收工具。

标注是外部数据源的输入，不应靠“能启动”判断正确。本模块检查标注中的表、列、
角色和别名是否与实时 Schema 一致，并报告未覆盖字段与别名冲突，供部署前验收。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import TableInfo


_ROLES = {"metric", "dimension"}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def validate_schema_annotations(
    tables: tuple[TableInfo, ...],
    aliases_path: str | Path,
) -> dict[str, Any]:
    path = Path(aliases_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        version = payload.get("version", 1)
        rules = payload.get("rules")
    else:
        version = 1
        rules = payload
    if version != 1:
        raise ValueError("Schema 标注 version 必须为 1")
    if not isinstance(rules, list):
        raise ValueError("Schema 标注必须是数组或 {version, rules} 对象")

    by_table = {table.name: table for table in tables}
    columns = {table.name: {column.name for column in table.columns} for table in tables}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    aliases_seen: dict[str, tuple[str, str]] = {}
    annotated: set[tuple[str, str]] = set()
    normalized_rules: list[dict[str, Any]] = []
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            errors.append({"code": "invalid_rule", "index": index, "message": "规则必须是对象"})
            continue
        table = str(item.get("table", "")).strip()
        column = str(item.get("column", "")).strip()
        role = str(item.get("role", "")).strip().lower()
        aliases = item.get("aliases", [])
        if table not in by_table:
            errors.append({"code": "unknown_table", "index": index, "table": table})
            continue
        if column not in columns[table]:
            errors.append({"code": "unknown_column", "index": index, "table": table, "column": column})
            continue
        if role not in _ROLES:
            errors.append({"code": "invalid_role", "index": index, "role": role})
            continue
        if not isinstance(aliases, list) or not aliases or not all(isinstance(alias, str) and alias.strip() for alias in aliases):
            errors.append({"code": "invalid_aliases", "index": index, "table": table, "column": column})
            continue
        annotated.add((table, column))
        clean_aliases = tuple(dict.fromkeys(alias.strip() for alias in aliases))
        normalized_rules.append({"table": table, "column": column, "role": role, "aliases": list(clean_aliases)})
        for alias in clean_aliases:
            normalized = _normalize(alias)
            previous = aliases_seen.get(normalized)
            if previous and previous != (table, column):
                errors.append({
                    "code": "conflicting_alias",
                    "alias": alias,
                    "first": {"table": previous[0], "column": previous[1]},
                    "second": {"table": table, "column": column},
                })
            else:
                aliases_seen[normalized] = (table, column)

    for table in tables:
        for column in table.columns:
            if (table.name, column.name) not in annotated:
                warnings.append({"code": "unannotated_column", "table": table.name, "column": column.name})
    status = "invalid" if errors else ("valid_with_warnings" if warnings else "valid")
    return {
        "status": status,
        "source": str(path),
        "version": version,
        "rule_count": len(normalized_rules),
        "annotated_column_count": len(annotated),
        "schema_table_count": len(tables),
        "errors": errors,
        "warnings": warnings,
    }
