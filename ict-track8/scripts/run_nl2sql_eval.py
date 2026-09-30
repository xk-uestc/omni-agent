"""运行赛题八 NL2SQL 回归集并输出 JSON 报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.evaluation import evaluate, load_cases
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    database = PACKAGE_ROOT / "data" / "eval.sqlite"
    initialize_database(database)
    report = evaluate(Nl2SqlEngine(database), load_cases(PACKAGE_ROOT / "data" / "nl2sql_eval_cases.json"))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
