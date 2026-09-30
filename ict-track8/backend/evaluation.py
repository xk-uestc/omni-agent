"""赛题八结构化问数的可复现评测契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .nl2sql.engine import Nl2SqlEngine


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    expected_status: str
    sql_contains: tuple[str, ...] = ()
    parameters_contains: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = ()
    clarification_code: str | None = None
    min_rows: int | None = None

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> "EvalCase":
        return cls(
            case_id=str(item["case_id"]),
            question=str(item["question"]),
            expected_status=str(item["expected_status"]),
            sql_contains=tuple(str(value) for value in item.get("sql_contains", [])),
            parameters_contains=tuple(str(value) for value in item.get("parameters_contains", [])),
            expected_columns=tuple(str(value) for value in item.get("expected_columns", [])),
            clarification_code=item.get("clarification_code"),
            min_rows=item.get("min_rows"),
        )


def load_cases(path: str | Path) -> tuple[EvalCase, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(EvalCase.from_dict(item) for item in payload)


def evaluate(engine: Nl2SqlEngine, cases: Iterable[EvalCase]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        checks: list[str] = []
        try:
            result = engine.answer(case.question)
            if result.status != case.expected_status:
                checks.append(f"status expected={case.expected_status} actual={result.status}")
            if case.sql_contains and not result.sql:
                checks.append("SQL missing")
            if result.sql:
                checks.extend(
                    f"SQL missing token: {token}"
                    for token in case.sql_contains
                    if token not in result.sql
                )
            actual_parameters = {str(value) for value in result.parameters}
            checks.extend(
                f"parameter missing: {value}"
                for value in case.parameters_contains
                if value not in actual_parameters
            )
            if case.expected_columns and tuple(result.columns) != case.expected_columns:
                checks.append(f"columns expected={case.expected_columns} actual={result.columns}")
            if case.clarification_code and result.clarification_code != case.clarification_code:
                checks.append(f"clarification expected={case.clarification_code} actual={result.clarification_code}")
            if case.min_rows is not None and len(result.rows) < case.min_rows:
                checks.append(f"rows expected>={case.min_rows} actual={len(result.rows)}")
            actual = result.to_dict()
        except Exception as exc:  # keep all cases visible in a batch report
            checks.append(f"exception: {type(exc).__name__}: {exc}")
            actual = {"status": "exception"}
        records.append({"case_id": case.case_id, "question": case.question, "passed": not checks, "checks": checks, "actual": actual})
    passed = sum(record["passed"] for record in records)
    total = len(records)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": records,
    }
