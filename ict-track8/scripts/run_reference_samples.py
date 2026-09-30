"""运行一组固定的赛题八参考样例并输出可复核 JSON。"""

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


REFERENCE_CASES = (
    "industry_sales_region_category",
    "industry_rank",
    "industry_high_customer",
    "industry_missing_metric",
    "industry_yoy",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "reference-samples.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "reference-samples-report.json")
    args = parser.parse_args()

    database = initialize_industry_database(args.database)
    engine = Nl2SqlEngine(database, aliases_path=ROOT / "data" / "industry_aliases.json")
    all_cases = load_cases(ROOT / "data" / "industry_nl2sql_eval_cases.json")
    selected = [case for case in all_cases if case.case_id in REFERENCE_CASES]
    if len(selected) != len(REFERENCE_CASES):
        found = {case.case_id for case in selected}
        missing = [case_id for case_id in REFERENCE_CASES if case_id not in found]
        raise SystemExit("参考样例缺失: " + ", ".join(missing))

    report = evaluate(engine, selected)
    report.update({
        "report_type": "ict8_reference_samples",
        "selection": list(REFERENCE_CASES),
        "database": database.name,
        "dataset": "industry_nl2sql_eval_cases.json",
        "model_api_used": False,
        "model_api_note": "本报告使用确定性规则规划器；未调用外部模型，结果可离线复现。",
    })
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
