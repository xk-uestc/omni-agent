"""防硬编码检查：评测题目/用例 ID 不得出现在被测源码与业务配置中。

  python eval/check_no_hardcode.py            # 扫描默认范围，发现即退出码 1
  python eval/check_no_hardcode.py --quiet    # 只在失败时输出（适合 Claude Code 钩子）

检查范围：backend/**/*.py、data/*aliases*.json、data/*value*.json（业务配置）。
不检查 tests/ 与 eval/（测试本来就要引用题目）。
判据：题目归一化后长度 ≥ 6 且作为子串出现；用例 ID 作为完整标识符出现。
短语级别的业务词（如"销售额"）属于合法词典，不在检查范围。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def load_questions() -> tuple[set[str], set[str]]:
    questions, ids = set(), set()
    for path in sorted((ROOT / "eval" / "cases").glob("*.json")) + sorted((ROOT / "data").glob("*eval_cases*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
        for case in cases:
            ids.add(str(case.get("id") or case.get("case_id") or ""))
            for question in [case.get("question")] + [t.get("question") for t in case.get("turns", [])]:
                if question and len(_normalize(question)) >= 6:
                    questions.add(_normalize(question))
    ids.discard("")
    return questions, ids


def scan() -> list[str]:
    questions, ids = load_questions()
    targets = list((ROOT / "backend").rglob("*.py")) + [
        p for p in (ROOT / "data").glob("*.json") if "alias" in p.name or "value" in p.name
    ]
    # 只检查结构化 ID（S01_…、G7_…）；旧用例 ID 是 industry/threshold 这类普通词，与列名重合，不做 ID 检查
    structured = sorted(case_id for case_id in ids if re.match(r"^(?:[A-Z]\d{2}_|G\d+_)", case_id))
    id_patterns = [(case_id, re.compile(r"(?<![A-Za-z0-9_])" + re.escape(case_id) + r"(?![A-Za-z0-9_])")) for case_id in structured]
    findings = []
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="ignore")
        compact = _normalize(text)
        for question in questions:
            if question in compact:
                findings.append(f"{path.relative_to(ROOT)}: 包含评测原题 “{question}”")
        for case_id, pattern in id_patterns:
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: 包含用例 ID “{case_id}”")
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    findings = scan()
    if findings:
        print("防硬编码检查失败：\n" + "\n".join(f"- {item}" for item in findings), file=sys.stderr)
        sys.exit(1)
    if not args.quiet:
        questions, ids = load_questions()
        print(f"防硬编码检查通过：{len(questions)} 道题目、{len(ids)} 个用例 ID 均未出现在 backend/ 与业务配置中。")


if __name__ == "__main__":
    main()
