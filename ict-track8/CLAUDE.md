# CLAUDE.md — 赛题八本地评测工作流

本文件供 Claude Code（终端/桌面）在本仓库工作时读取。目标：任何一次代码改动，
都能在几分钟内量化"是否比上一版更好"，而不是凭感觉判断。

## 硬性规则（务必遵守）

1. **禁止为评测题库写死答案。** 不允许在 `backend/` 或 `data/` 中出现题库的问题原文
   或用例 id。每次改动后运行 `python eval/check_no_hardcode.py` 校验，CI 门禁会拦截。
2. **RAG / 知识库检索模块不在本次优化范围内**，除非用户明确要求，否则不要修改
   `backend/document_analysis.py` 之外的检索链路、向量化或知识库问答逻辑。
3. **绝不读取、打印或提交任何密钥/凭证文件**：`backend/config_runtime.py`、
   `web-client/.env.example`、`monitor-alerts.env.example`、任何 `.env*`、`*secret*`。
   `.claude/settings.json` 已将这些路径加入拒绝读取列表。
4. 任何 SQL 生成路径的改动，必须能说明其对应哪个真实语言现象或 Schema 结构
   （时间表达式、否定、单位、JOIN 基数……），不写针对某一测试问题的特判。
5. 修改后必须能通过：单元测试 + `run_eval.py` 全量评测 + `compare_reports.py` 门禁
   （硬回归、静默错误率上升、安全违规、数据库被写入，任一项即失败）。

## 目录速览

- `backend/nl2sql/` — NL2SQL 规划与执行（`lexicon.py` 词法层，`value_index.py` 实体值
  链接，`planner.py` 规则规划器，`engine.py` 编排与多轮改写，`model_contract.py` 模型
  计划校验）。
- `backend/document_analysis.py` / `backend/pdf_ingest.py` / `backend/ocr.py` /
  `backend/image_quality.py` — 非结构化文档链路（本次优化范围内）。
- `backend/app.py` — FastAPI 入口；TestClient 和真实 Uvicorn 冒烟均纳入本地验收。
- `eval/` — 评测框架，见下。

## 评测框架

```
eval/
  db_variants.py            种子/合成/定向扰动三种数据库变体（不含任何答案）
  build_cases.py            生成 eval/cases/nl2sql_v2.json（题库：问题+金标SQL，无答案）
  generate_compositional.py 盲测生成集：槽位随机组合，检验是否过拟合题库
  run_eval.py               执行评测：跑被测系统 + 跑金标SQL，比较执行结果
  compare_reports.py        两份报告对比，输出回归/修复清单，作为门禁
  check_no_hardcode.py      静态扫描：题库问题/用例id不得出现在 backend/ 中
  bench_scale.py            1k/10k/100k 行、10/100/500 页的性能与吞吐量实测
  ocr_eval.py               合成退化图片 A/B：CER、pipeline 通过率
```

### 常用命令

```bash
# 0. 生成/刷新题库（改了 build_cases.py 之后执行一次）
python eval/build_cases.py
python eval/generate_compositional.py --seed 20260922 --n 150   # 盲测集，别在这上面调参

# 1. 建一份未修改的基线树（只需一次）
git worktree add ../ict8-baseline <baseline-commit-或-origin/main>

# 2. 分别评测基线与当前工作树
python eval/run_eval.py --repo ../ict8-baseline/ict-track8 --out /tmp/baseline.json
python eval/run_eval.py --repo .                            --out /tmp/candidate.json

# 3. 对比，作为提交前的门禁（非 0 退出码 = 有回归，禁止提交）
python eval/compare_reports.py /tmp/baseline.json /tmp/candidate.json --markdown /tmp/compare.md

# 4. 防硬编码
python eval/check_no_hardcode.py

# 5. 单元测试（fastapi 三个模块在无网络沙箱不可运行，其余全跑）
python -m pytest tests/ -q          # 有 pytest 时
python /path/to/shim/run_tests.py tests   # 无网络沙箱内的等价替代

# 6. 性能与 OCR（可选，用于技术文档"系统效率"章节）
python eval/bench_scale.py --repo . --out /tmp/bench.json --fail-on-error
python eval/ocr_eval.py --repo .   --out /tmp/ocr.json
```

`run_eval.py --split test` 使用按 case id 哈希切出的保留集（约 30%），可用于"不看结果调参"
的最终验收；日常调试用 `--split dev` 或默认 `all`。

## Slash 命令

- `/eval-baseline` — 首次建立基线 worktree 并跑一次基线评测。
- `/eval-compare` — 跑当前工作树评测并与基线对比，打印门禁结论。
- `/fix-case <case_id>` — 定位某个失败用例：打印它的问题、金标 SQL、被测系统的实际
  SQL/结果/澄清码，并给出该 case 命中的 `backend/nl2sql/` 源码位置，方便定点修复。

## 报告解读

- `EX_answerable`：可回答问题里执行结果正确的比例（映射赛题"NL2SQL 执行准确率"）。
- `EX_complex` / `EX_simple_single_turn`：分别对应决赛"复杂查询准确率"与初赛"单表准确率"
  的口径（含 JOIN/嵌套/比较/窗口函数记为 complex）。
- `silent_error_rate`：状态是 ok 但结果错误的比例——这是本轮审查最关心的指标，
  子串匹配的旧评测无法发现这类问题。
- `reliability_score_RS`：TrustSQL 风格的可靠性评分，静默错误的惩罚系数默认为 5。
- `dialogue_success`：多轮场景全部轮次都正确的比例（映射"连续 5 轮以上准确"）。
- `database_files_modified`：非空即表示评测过程中数据库被写入，属于严重问题
  （只读安全门失效）。
