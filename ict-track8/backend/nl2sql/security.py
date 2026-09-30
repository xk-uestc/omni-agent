"""只读 SQL 校验和 SQLite 执行安全门。"""

from __future__ import annotations

import re
import time
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SqlSafetyError(ValueError):
    """SQL 未通过只读安全策略。"""


_FORBIDDEN = re.compile(
    r"\b(?:attach|detach|pragma|vacuum|reindex|analyze|create|drop|alter|insert|update|delete|replace|"
    r"truncate|grant|revoke|load_extension)\b",
    re.IGNORECASE,
)
_MAX_SQL_CHARS = 100_000
_WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}


@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    max_rows: int


def validate_read_only_sql(sql: str, max_rows: int = 100) -> ValidatedSql:
    if not isinstance(sql, str) or not sql.strip():
        raise SqlSafetyError("SQL 不能为空")
    if "\x00" in sql or "--" in sql or "/*" in sql or "*/" in sql:
        raise SqlSafetyError("SQL 不允许注释或 NUL 字符")
    compact = sql.strip()
    if len(compact) > _MAX_SQL_CHARS:
        raise SqlSafetyError(f"SQL 超过长度上限（{_MAX_SQL_CHARS} 字符）")
    if compact.endswith(";"):
        compact = compact[:-1].rstrip()
    try:
        statements = [statement for statement in parse(compact, read="sqlite") if statement is not None]
    except ParseError as exc:
        raise SqlSafetyError("SQL 无法按 SQLite 方言解析") from exc
    if len(statements) != 1:
        raise SqlSafetyError("只允许执行一条 SQL")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise SqlSafetyError("只允许 SELECT 或只读 WITH 查询")
    if any(statement.find(node) is not None for node in (exp.DML, exp.DDL, exp.Command, exp.Into)):
        raise SqlSafetyError("SQL 包含禁止的写入或管理操作")
    if any(
        isinstance(function, exp.Anonymous) and function.name.lower() in {"load_extension", "writefile"}
        for function in statement.walk()
    ):
        raise SqlSafetyError("SQL 包含禁止的扩展或文件写入函数")
    if not 1 <= int(max_rows) <= 1000:
        raise SqlSafetyError("max_rows 必须在 1 到 1000 之间")
    return ValidatedSql(sql=compact, max_rows=int(max_rows))


def _authorizer(action: int, _arg1: str | None, _arg2: str | None, _db: str | None, _source: str | None) -> int:
    if action in _WRITE_ACTIONS or action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (_arg2 or _arg1 or "").lower() in {"load_extension", "writefile"}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def execute_read_only(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
    *,
    max_rows: int = 100,
    max_steps: int = 200_000,
    max_seconds: float | None = None,
) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
    """只读执行。max_seconds 为墙钟预算（主保护，随数据量增长仍然有意义）；
    max_steps 为 VM 步数硬上限（兜底，防止极端查询长时间占用 CPU）。"""

    validated = validate_read_only_sql(sql, max_rows=max_rows)
    deadline = (time.monotonic() + float(max_seconds)) if max_seconds else None
    connection.set_authorizer(_authorizer)
    connection.set_progress_handler(lambda: 1 if False else 0, 0)
    steps = 0

    def progress() -> int:
        nonlocal steps
        steps += 1_000
        if steps > max_steps:
            return 1
        return 1 if deadline is not None and time.monotonic() > deadline else 0

    connection.set_progress_handler(progress, 1_000)
    try:
        cursor = connection.execute(validated.sql, tuple(parameters))
        rows = cursor.fetchmany(validated.max_rows + 1)
        if len(rows) > validated.max_rows:
            raise SqlSafetyError("查询结果超过安全行数上限")
        columns = tuple(column[0] for column in cursor.description or ())
        return columns, tuple({column: row[index] for index, column in enumerate(columns)} for row in rows)
    except sqlite3.DatabaseError as exc:
        raise SqlSafetyError(f"只读 SQL 执行失败: {exc}") from exc
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)
