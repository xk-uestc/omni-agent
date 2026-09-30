#!/usr/bin/env bash
# 一键评测：基线 vs 当前工作区，输出报告目录并执行回归门禁。
#
# 用法（在仓库根目录，即包含 ict-track8/ 的目录执行）：
#   bash ict-track8/eval/run_all.sh                 # 基线 = git 引用 ict8-baseline
#   BASE_REF=0359315 bash ict-track8/eval/run_all.sh
#   BASE_DIR=/path/to/original/ict-track8 bash ict-track8/eval/run_all.sh   # 没有 git 历史时直接指定原始代码目录
#   BENCH=1 OCR=1 bash ict-track8/eval/run_all.sh   # 追加规模基准与 OCR A/B（较慢；基准失败会阻断）
#
# 环境变量：PYTHON（默认 python3）、GEN_SEED（盲测集随机种子，默认 20260922）、STRICT=1（软回归也失败）
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PY="${PYTHON:-python3}"
EVAL="$ROOT/ict-track8/eval"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/eval-reports/$STAMP"
mkdir -p "$OUT"

# ---------- 1. 准备基线代码目录
if [[ -n "${BASE_DIR:-}" ]]; then
  BASE="$BASE_DIR"
else
  BASE_REF="${BASE_REF:-ict8-baseline}"
  WT="$ROOT/../.ict8-baseline-worktree"
  if [[ ! -d "$WT" ]]; then
    git -C "$ROOT" worktree add --detach "$WT" "$BASE_REF" >/dev/null
  else
    git -C "$WT" checkout --detach -q "$BASE_REF"
  fi
  BASE="$WT/ict-track8"
fi
CAND="$ROOT/ict-track8"
echo "基线: $BASE"
echo "候选: $CAND"
echo "报告: $OUT"

# ---------- 2. 静态门禁
"$PY" "$EVAL/check_no_hardcode.py" | tee "$OUT/no_hardcode.txt"
if [[ "${SKIP_PYTEST:-0}" != "1" ]]; then
  (cd "$CAND" && "$PY" -m pytest -q 2>&1 | tail -3) | tee "$OUT/pytest.txt"
fi

# ---------- 3. 题库评测（主题库 + 组合泛化盲测集）
SEED="${GEN_SEED:-20260922}"
"$PY" "$EVAL/generate_compositional.py" --seed "$SEED" >/dev/null
GEN="$EVAL/cases/generated_seed$SEED.json"
for SIDE in base cand; do
  REPO="$BASE"; [[ "$SIDE" == cand ]] && REPO="$CAND"
  "$PY" "$EVAL/run_eval.py" --repo "$REPO" --out "$OUT/$SIDE.json" >/dev/null
  "$PY" "$EVAL/run_eval.py" --repo "$REPO" --cases "$GEN" --out "$OUT/${SIDE}_generated.json" >/dev/null
  "$PY" "$EVAL/run_eval.py" --repo "$REPO" --split test --out "$OUT/${SIDE}_test_split.json" >/dev/null
done

STRICT_FLAG=""; [[ "${STRICT:-0}" == "1" ]] && STRICT_FLAG="--strict"
set +e
"$PY" "$EVAL/compare_reports.py" "$OUT/base.json" "$OUT/cand.json" --markdown "$OUT/compare_main.md" $STRICT_FLAG
MAIN_RC=$?
"$PY" "$EVAL/compare_reports.py" "$OUT/base_generated.json" "$OUT/cand_generated.json" --markdown "$OUT/compare_generated.md" $STRICT_FLAG >/dev/null
GEN_RC=$?
"$PY" "$EVAL/compare_reports.py" "$OUT/base_test_split.json" "$OUT/cand_test_split.json" --markdown "$OUT/compare_test_split.md" $STRICT_FLAG >/dev/null
set -e

# ---------- 4. 可选：规模基准 与 OCR A/B
if [[ "${BENCH:-0}" == "1" ]]; then
  "$PY" "$EVAL/bench_scale.py" --repo "$BASE" --out "$OUT/bench_base.json" --fail-on-error
  "$PY" "$EVAL/bench_scale.py" --repo "$CAND" --out "$OUT/bench_cand.json" --fail-on-error
fi
if [[ "${OCR:-0}" == "1" ]]; then
  "$PY" "$EVAL/ocr_eval.py" --repo "$BASE" --out "$OUT/ocr_base.json"
  "$PY" "$EVAL/ocr_eval.py" --repo "$CAND" --out "$OUT/ocr_cand.json"
fi

echo
echo "主题库门禁: $([[ $MAIN_RC == 0 ]] && echo 通过 || echo 未通过)；盲测集门禁: $([[ $GEN_RC == 0 ]] && echo 通过 || echo 未通过)"
echo "明细: $OUT"
exit $(( MAIN_RC || GEN_RC ))
