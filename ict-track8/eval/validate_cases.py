"""Validate evaluation case metadata before running a benchmark."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED = {"id", "category", "db", "question", "expected", "tags", "variants", "safety"}


def validate(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids: set[str] = set()
    independent = payload.get("provenance") == "independent_manual_review_required"
    for index, case in enumerate(payload.get("cases", [])):
        # dialogue 用 turns 表达问题，顶层 question 允许为空或缺省；普通单轮才要求 question。
        required = REQUIRED - ({"question"} if case.get("expected") == "dialogue" else set())
        missing = required - set(case)
        if missing:
            errors.append(f"case[{index}] missing: {sorted(missing)}")
        case_id = case.get("id")
        if case_id in ids:
            errors.append(f"duplicate id: {case_id}")
        ids.add(case_id)
        if case.get("expected") == "ok" and not case.get("gold_sql"):
            errors.append(f"{case_id}: expected ok requires gold_sql")
        if case.get("expected") == "clarification" and case.get("gold_sql"):
            errors.append(f"{case_id}: clarification must not have gold_sql")
        if case.get("expected") == "dialogue":
            turns = case.get("turns", [])
            if len(turns) < 2:
                errors.append(f"{case_id}: dialogue requires at least two turns")
            for turn_index, turn in enumerate(turns, start=1):
                if turn.get("expected") == "ok" and not turn.get("gold_sql"):
                    errors.append(f"{case_id} turn {turn_index}: expected ok requires gold_sql")
        if independent and (not case.get("tags") or "blind" not in case.get("tags", [])):
            errors.append(f"{case_id}: independent cases must include blind tag")
    return errors


if __name__ == "__main__":
    errors = validate(Path(sys.argv[1]))
    if errors:
        print("\n".join(errors))
        raise SystemExit(1)
    print("case metadata valid")
