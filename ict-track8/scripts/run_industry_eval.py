"""运行行业级 Schema 评测并输出可归档 JSON 报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evaluation import evaluate, load_cases
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.industry_seed import initialize_industry_database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "industry_eval.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "industry-eval-report.json")
    args = parser.parse_args()
    database = initialize_industry_database(args.database)
    engine = Nl2SqlEngine(database, aliases_path=ROOT / "data" / "industry_aliases.json")
    report = evaluate(engine, load_cases(ROOT / "data" / "industry_nl2sql_eval_cases.json"))
    report["dataset"] = "industry_nl2sql_eval_cases.json"
    report["database"] = database.name
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output + "\n", encoding="utf-8")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
