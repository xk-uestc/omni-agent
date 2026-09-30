"""对比两份 run_eval 报告，输出指标差值与逐题状态迁移；作为回归门禁使用。

  python eval/compare_reports.py base.json cand.json [--markdown out.md]

退出码：0 = 通过；1 = 存在回归（correct→非 correct）、静默错误率上升、
安全违规或数据库文件被修改。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

KEYS = [
    "EX_answerable", "EX_simple_single_turn", "EX_complex", "silent_error_rate", "abstain_rate_on_answerable",
    "clarification_recall", "clarification_precision", "reliability_score_RS", "dialogue_success", "safety_violations", "errors",
]


def _index(report):
    """Index executable records and reject duplicate evaluation coordinates."""

    result = {}
    duplicates = []
    for record in report["records"]:
        if record["kind"] == "dialogue_summary":
            continue
        key = (record["id"], record["variant"], record.get("turn"))
        if key in result:
            duplicates.append(key)
        result[key] = record
    if duplicates:
        raise ValueError(f"评测报告包含重复单元: {duplicates[:3]}")
    return result


def compare(base: dict, cand: dict, strict: bool = False) -> tuple[str, bool]:
    b, c = base["summary"], cand["summary"]
    lines = ["| 指标 | 基线 | 候选 | 变化 |", "|---|---|---|---|"]
    for key in KEYS:
        x, y = b.get(key), c.get(key)
        delta = "" if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) else f"{y - x:+.4f}"
        lines.append(f"| {key} | {x} | {y} | {delta} |")
    for key, value in c.get("competition_mapping", {}).items():
        if key not in {"note", "availability"}:
            lines.append(f"| {key} | {b.get('competition_mapping', {}).get(key)} | {value} | |")
    unavailable = [key for key, present in c.get("competition_mapping", {}).get("availability", {}).items() if not present]
    if unavailable:
        lines.append("\n未覆盖的竞赛映射项（不是 0 分）：" + "、".join(unavailable))
    try:
        bi, ci = _index(base), _index(cand)
    except ValueError as exc:
        lines.append(f"\n门禁失败：{exc}")
        return "\n".join(lines), True
    missing = sorted(set(bi) - set(ci), key=str)
    unexpected = sorted(set(ci) - set(bi), key=str)
    if missing:
        lines.append(f"\n门禁失败：候选报告缺少 {len(missing)} 个评测单元（示例：{missing[:3]}）。")
    if unexpected:
        lines.append(f"\n门禁失败：候选报告新增 {len(unexpected)} 个评测单元（示例：{unexpected[:3]}）。")
    transitions = Counter()
    regressions, fixes = [], []
    for key in sorted(set(bi) & set(ci), key=str):
        before, after = bi[key]["outcome"], ci[key]["outcome"]
        transitions[(before, after)] += 1
        if before == "correct" and after != "correct":
            regressions.append((key, after, ci[key]["detail"]))
    hard = [item for item in regressions if item[1] in {"silent_error", "error"}]
    soft = [item for item in regressions if item[1] == "abstain"]
    if True:
        pass
    for key in sorted(set(bi) & set(ci), key=str):
        if bi[key]["outcome"] != "correct" and ci[key]["outcome"] == "correct":
            fixes.append((key, bi[key]["outcome"]))
    lines += ["", "状态迁移（基线 → 候选）：", ""]
    lines += [f"- {a} → {bb}: {n}" for (a, bb), n in sorted(transitions.items())]
    lines += ["", f"修复 {len(fixes)} 个评测单元；硬回归（正确→静默错误/异常）{len(hard)} 个；软回归（正确→澄清）{len(soft)} 个。"]
    for title, items in (("硬回归明细：", hard), ("软回归明细（安全但体验下降，默认只告警，--strict 时拦截）：", soft)):
        if items:
            lines += ["", title] + [f"- {k[0]} [{k[1]}{'' if k[2] is None else ' turn ' + str(k[2])}] → {o}: {d[:160]}" for k, o, d in items]
    failed = bool(missing or unexpected or hard) or (strict and bool(soft))
    if (c.get("silent_error_rate") or 0) > (b.get("silent_error_rate") or 0):
        lines.append("\n门禁失败：静默错误率上升。")
        failed = True
    if c.get("safety_violations"):
        lines.append("\n门禁失败：存在安全违规 SQL。")
        failed = True
    if c.get("database_files_modified"):
        lines.append("\n门禁失败：评测期间数据库文件被修改。")
        failed = True
    lines.append("\n结论：" + ("未通过" if failed else "通过"))
    return "\n".join(lines), failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--strict", action="store_true", help="软回归（正确→澄清）也视为失败")
    args = parser.parse_args()
    text, failed = compare(json.loads(args.base.read_text(encoding="utf-8")), json.loads(args.candidate.read_text(encoding="utf-8")), args.strict)
    print(text)
    if args.markdown:
        args.markdown.write_text(text + "\n", encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
