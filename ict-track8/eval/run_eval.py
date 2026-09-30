"""执行准确率评测（可评测任意版本的 ict-track8）。

用法：
  python eval/run_eval.py --repo <ict-track8 目录> --out report.json [--split all|dev|test]

判定逻辑（不比较 SQL 字符串，只比较执行结果）：
- expected=ok      ：被测系统 status=ok，且结果行（多重集；order_sensitive 时按序）
                     与金标 SQL 在同一数据库变体上的执行结果一致。被测结果允许多出
                     列（如 排名、占比），比对时在其列中寻找与金标列一一对应的投影。
- expected=empty   ：status=ok 且显式给出空结果信号（result_state=empty，或没有行）。
- expected=clarification：status=clarification（allowed_codes 用于报告编码精确率）。
- 多轮 dialogue    ：逐轮判定，上一轮 effective_question 作为下一轮上下文（与 app.py 一致）。

结局分类与可靠性评分（参考 TrustSQL 的惩罚式评分思想）：
  correct           +1   正确回答 / 正确澄清
  abstain            0   可回答的问题选择了澄清（安全但未完成）
  silent_error      -c   status=ok 但结果错误，或对应澄清的问题直接给出了答案
  error             -c   异常
RS(c) = mean(score)。默认 c=5：一次静默错误的代价远高于一次澄清。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import math
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db_variants  # noqa: E402

_WRITE_RE = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum)\b", re.I)


# --------------------------------------------------------------------------- loading
def load_backend(repo: Path):
    repo = repo.resolve()
    sys.path.insert(0, str(repo))
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]
    backend = importlib.import_module("backend")
    for module in ("backend.nl2sql.engine", "backend.nl2sql.seed", "backend.nl2sql.industry_seed", "backend.cross_source", "backend.nl2sql.security"):
        importlib.import_module(module)
    return backend


def make_engine(backend, db_path: Path, kind: str, repo: Path, reference: date):
    Engine = backend.nl2sql.engine.Nl2SqlEngine
    kwargs = {}
    if kind == "industry":
        kwargs["aliases_path"] = repo / "data" / "industry_aliases.json"
    if "reference_date" in inspect.signature(Engine.__init__).parameters:
        kwargs["reference_date"] = reference
    return Engine(db_path, **kwargs)


# --------------------------------------------------------------------------- comparison
def _norm(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value)):
            return None
        return round(float(value), 6)
    if value is None:
        return None
    return str(value).strip()


def _row_key(row):
    return tuple((0, "") if v is None else ((1, v) if isinstance(v, float) else (2, str(v))) for v in row)


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
    return a == b


def rows_match(pred_rows: list[tuple], gold_rows: list[tuple], ordered: bool, *, projection=None) -> bool:
    if len(pred_rows) != len(gold_rows):
        return False
    if not gold_rows:
        return True
    width_gold = len(gold_rows[0])
    width_pred = len(pred_rows[0]) if pred_rows else 0
    if width_pred < width_gold:
        return False
    gold = [tuple(_norm(v) for v in row) for row in gold_rows]
    columns = tuple(projection) if projection is not None else tuple(range(width_gold))
    if len(columns) != width_gold or len(set(columns)) != len(columns) or any(i < 0 or i >= width_pred for i in columns):
        return False
    projected = [tuple(_norm(row[i]) for i in columns) for row in pred_rows]
    pairs = zip(projected, gold) if ordered else zip(sorted(projected, key=_row_key), sorted(gold, key=_row_key))
    return all(all(_same(x, y) for x, y in zip(p, g)) for p, g in pairs)


# --------------------------------------------------------------------------- single evaluation
def judge(expected: str, response: dict, gold_rows, ordered: bool, allowed_codes, *, result_schema=None) -> tuple[str, str]:
    status = response.get("status")
    if status == "clarification":
        if expected == "clarification":
            code_ok = not allowed_codes or response.get("clarification_code") in allowed_codes
            return ("correct", "clarified") if code_ok else ("error", f"wrong clarification code: {response.get('clarification_code')} not in {allowed_codes}")
        return "abstain", f"clarified with {response.get('clarification_code')}"
    if status != "ok":
        return "error", f"status={status}"
    if expected == "clarification":
        return "silent_error", "answered a question that required clarification"
    rows = response.get("rows") or []
    column_names = response.get("columns") or (list(rows[0]) if rows and isinstance(rows[0], dict) else [])
    values = [tuple(row.get(c) for c in column_names) if isinstance(row, dict) else tuple(row) for row in rows]
    if expected == "empty":
        explicit = not values or response.get("result_state") == "empty" and all(all(v is None for v in row) for row in values)
        return ("correct", "empty signalled") if explicit else ("silent_error", f"no empty signal; rows={values[:2]}")
    projection = None
    if result_schema is not None:
        projection = []
        audit = (response.get("plan") or {}).get("grain_audit") or {}
        units = {m.get("metric_id"): (m.get("unit"), m.get("currency")) for m in audit.get("metrics", []) + audit.get("formulas", [])}
        for field in result_schema:
            aliases = field.get("aliases", []) + [field["label"]]
            matches = [i for i, col in enumerate(column_names) if col in aliases]
            if len(matches) != 1:
                return "silent_error", f"missing/ambiguous result field: {field['label']}"
            projection.append(matches[0])
            if "metric_id" in field and (units.get(field["metric_id"]) != (field.get("unit"), field.get("currency"))):
                return "silent_error", f"result unit/currency mismatch: {field['label']}"
    if rows_match(values, gold_rows, ordered, projection=projection):
        return "correct", "rows match"
    return "silent_error", f"pred={values[:3]} gold={gold_rows[:3]}"


def split_of(case_id: str, *, group: str | None = None) -> str:
    # 新题必须提供 split_group（Schema/模板族/来源）；旧题保留可复现的 ID 拆分。
    return "test" if int(hashlib.sha1((group or case_id).encode()).hexdigest(), 16) % 10 < 3 else "dev"


def run(repo: Path, cases_path: Path, split: str, c_penalty: float) -> dict:
    backend = load_backend(repo)
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    reference = date.fromisoformat(payload["reference_date"])
    validate_sql = backend.nl2sql.security.validate_read_only_sql
    Agent = backend.cross_source.CrossSourceAgent
    Retriever = backend.cross_source.JsonDocumentRetriever
    docs = Retriever.from_file(repo / "data" / "knowledge_documents.json")
    records, latencies = [], []
    workdir = Path(tempfile.mkdtemp(prefix="ict8-eval-"))
    engines: dict[tuple[str, str], tuple] = {}

    def engine_for(kind, variant):
        key = (kind, variant)
        if key not in engines:
            path = workdir / f"{kind}-{variant}.sqlite"
            (db_variants.build_sales if kind == "sales" else db_variants.build_industry)(backend, variant, path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            engines[key] = (make_engine(backend, path, kind, repo, reference), path, digest)
        return engines[key]

    def gold(path, sql):
        if not sql:
            return []
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            return connection.execute(sql).fetchall()

    for case in payload["cases"]:
        if split != "all" and split_of(case["id"], group=case.get("split_group")) != split:
            continue
        for variant in case["variants"]:
            engine, path, _ = engine_for(case["db"], variant)
            if case["expected"] == "dialogue":
                agent = Agent(engine, docs)
                context, turn_outcomes = (), []
                for index, turn in enumerate(case["turns"], start=1):
                    started = time.perf_counter()
                    try:
                        answer = agent.answer(turn["question"], context_questions=context)
                        response = answer["structured"]
                        effective = answer.get("effective_question") or turn["question"]
                        outcome, detail = judge(turn["expected"], response, gold(path, turn.get("gold_sql")), turn.get("order_sensitive", False), turn.get("allowed_codes"), result_schema=turn.get("result_schema"))
                    except Exception as exc:  # noqa: BLE001
                        response, effective = {}, turn["question"]
                        outcome, detail = "error", f"{type(exc).__name__}: {exc}"[:300]
                    latencies.append((time.perf_counter() - started) * 1000)
                    context = (effective,)
                    turn_outcomes.append(outcome)
                    records.append(_record(case, variant, outcome, detail, response, turn=index, question=turn["question"],
                                           effective=effective, validate_sql=validate_sql))
                records.append({"id": case["id"], "variant": variant, "kind": "dialogue_summary", "category": case["category"],
                                "tags": case["tags"], "turns": len(case["turns"]),
                                "outcome": "correct" if all(o == "correct" for o in turn_outcomes) else "silent_error",
                                "turn_outcomes": turn_outcomes, "split": split_of(case["id"], group=case.get("split_group"))})
                continue
            started = time.perf_counter()
            try:
                response = engine.answer(case["question"]).to_dict()
                outcome, detail = judge(case["expected"], response, gold(path, case.get("gold_sql")), case.get("order_sensitive", False), case.get("allowed_codes"), result_schema=case.get("result_schema"))
            except Exception as exc:  # noqa: BLE001
                response, outcome, detail = {}, "error", f"{type(exc).__name__}: {exc}"[:300]
            latencies.append((time.perf_counter() - started) * 1000)
            records.append(_record(case, variant, outcome, detail, response, question=case["question"], validate_sql=validate_sql))

    # 数据库只读性：执行前后的文件哈希必须一致
    mutated_files = [str(p.name) for (_, p, digest) in engines.values() if hashlib.sha256(p.read_bytes()).hexdigest() != digest]
    return summarize(records, latencies, c_penalty, repo, split, mutated_files)


def _record(case, variant, outcome, detail, response, *, question, validate_sql, turn=None, effective=None):
    sql = response.get("sql")
    safety_violation = False
    if sql:
        try:
            validate_sql(sql)
        except Exception:  # noqa: BLE001
            safety_violation = True
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "", sql)
        safety_violation = safety_violation or bool(_WRITE_RE.search(stripped))
    return {
        "id": case["id"], "variant": variant, "kind": "turn" if turn else "single", "turn": turn,
        "category": case["category"], "tags": case["tags"], "question": question, "effective_question": effective,
        "expected": case["expected"] if turn is None else case["turns"][turn - 1]["expected"],
        "outcome": outcome, "detail": detail, "status": response.get("status"),
        "clarification_code": response.get("clarification_code"), "sql": sql,
        "planner_source": (response.get("plan") or {}).get("planner_source"),
        "planner_audit": (response.get("plan") or {}).get("planner_audit", {}),
        "safety_case": bool(case.get("safety")), "safety_violation": safety_violation, "split": split_of(case["id"], group=case.get("split_group")),
        "result_validation": "schema_and_values" if (case if turn is None else case["turns"][turn - 1]).get("result_schema") else "positional_values_only",
        "split_strategy": "group" if case.get("split_group") else "legacy_case_id",
    }


def summarize(records, latencies, c, repo, split, mutated_files) -> dict:
    units = [r for r in records if r["kind"] != "dialogue_summary"]
    score = {"correct": 1.0, "abstain": 0.0, "silent_error": -c, "error": -c}

    def rate(items, predicate):
        return round(sum(1 for r in items if predicate(r)) / len(items), 4) if items else None

    answerable = [r for r in units if r["expected"] in {"ok", "empty"}]
    clar_expected = [r for r in units if r["expected"] == "clarification"]
    clar_issued = [r for r in units if r["status"] == "clarification"]
    complex_units = [r for r in answerable if {"join", "nested", "compare", "window", "multi_table"} & set(r["tags"])]
    simple_units = [r for r in answerable if r["category"] in {"legacy", "simple"} and not {"join", "nested", "compare", "window", "multi_table", "multi_turn"} & set(r["tags"])]
    dialogues = [r for r in records if r["kind"] == "dialogue_summary"]
    by_category = {}
    for category in sorted({r["category"] for r in units}):
        items = [r for r in units if r["category"] == category]
        by_category[category] = {"n": len(items), "correct": rate(items, lambda r: r["outcome"] == "correct"),
                                 "silent_error": rate(items, lambda r: r["outcome"] == "silent_error")}
    planner_counts = {}
    planner_rejection_reasons = {}
    for record in units:
        source = record.get("planner_source") or "unknown"
        planner_counts[source] = planner_counts.get(source, 0) + 1
        audit = record.get("planner_audit") or {}
        if audit.get("fallback"):
            code = audit.get("reason_code") or "unknown"
            planner_rejection_reasons[code] = planner_rejection_reasons.get(code, 0) + 1
    by_tag = {}
    tags = sorted({tag for record in units for tag in record.get("tags", [])})
    for tag in tags:
        items = [record for record in units if tag in record.get("tags", [])]
        by_tag[tag] = {
            "n": len(items),
            "correct": rate(items, lambda r: r["outcome"] == "correct"),
            "silent_error": rate(items, lambda r: r["outcome"] == "silent_error"),
            "clarification": rate(items, lambda r: r.get("status") == "clarification"),
        }
    ex = rate(answerable, lambda r: r["outcome"] == "correct")
    ex_complex = rate(complex_units, lambda r: r["outcome"] == "correct")
    ex_simple = rate(simple_units, lambda r: r["outcome"] == "correct")

    def points(accuracy, table):
        if accuracy is None:
            return None
        return next((p for threshold, p in table if accuracy >= threshold), 0)

    summary = {
        "repo": str(repo), "split": split, "units": len(units),
        "EX_answerable": ex, "EX_simple_single_turn": ex_simple, "EX_complex": ex_complex,
        "silent_error_rate": rate(units, lambda r: r["outcome"] == "silent_error"),
        "abstain_rate_on_answerable": rate(answerable, lambda r: r["outcome"] == "abstain"),
        "clarification_recall": rate(clar_expected, lambda r: r["outcome"] == "correct"),
        "clarification_precision": rate(clar_issued, lambda r: r["expected"] == "clarification"),
        "reliability_score_RS": round(sum(score[r["outcome"]] for r in units) / len(units), 4) if units else None,
        "rs_penalty_c": c,
        "dialogue_success": rate(dialogues, lambda r: r["outcome"] == "correct"),
        "dialogue_count": len(dialogues),
        "unique_case_count": len({r["id"] for r in units}),
        "unique_question_count": len({r["question"] for r in units if r.get("question")}),
        "result_schema_validated_units": sum(r.get("result_validation") == "schema_and_values" for r in answerable),
        "safety_violations": sum(1 for r in units if r["safety_violation"]),
        "database_files_modified": mutated_files,
        "errors": sum(1 for r in units if r["outcome"] == "error"),
        "latency_ms": {"p50": round(statistics.median(latencies), 3) if latencies else None,
                       "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 3) if latencies else None},
        "by_category": by_category,
        "planner_sources": planner_counts,
        "planner_rejection_reasons": planner_rejection_reasons,
        "by_tag": by_tag,
        # 赛题评分表映射（仅作参考：正式分数以组委会测试集为准）
        "competition_mapping": {
            "初赛_NL2SQL执行准确率(10分)": points(ex_simple, [(0.9, 10), (0.8, 8), (0.7, 6)]),
            "决赛_NL2SQL复杂查询准确率(10分)": points(ex_complex, [(0.9, 10), (0.8, 7), (0.7, 5)]),
            "初赛_多轮对话上下文保持(5分)": None,
            "note": "EX_simple 对应初赛单表/基础题；EX_complex 为含 JOIN/嵌套/比较/窗口的子集",
            "availability": {
                "初赛_NL2SQL执行准确率(10分)": bool(simple_units),
                "决赛_NL2SQL复杂查询准确率(10分)": bool(complex_units),
                "初赛_多轮对话上下文保持(5分)": bool(dialogues),
            },
        },
    }
    return {"summary": summary, "records": records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True, help="被测 ict-track8 目录")
    parser.add_argument("--cases", type=Path, default=HERE / "cases" / "nl2sql_v2.json")
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all")
    parser.add_argument("--penalty", type=float, default=5.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.repo, args.cases, args.split, args.penalty)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
