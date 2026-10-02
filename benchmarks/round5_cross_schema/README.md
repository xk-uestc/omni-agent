# 第五轮跨 Schema 56 turn 评测

✅ 当前题包版本为Sakila v2，工具版本为workers-v1，`baseline_ready=true`。来源、56 turn、评分器及工具冻结不变。后续已完成真实模型隔离基线与第六轮验收：14/56→15/56；独立2/36→3/36，会话12/20与整段0/4持平，失败与退步保留。报告见 `docs/SAKILA_ROUND6_ISOLATED_BASELINE_20261002.json`、`docs/SAKILA_ROUND6_OPTIMIZED_ACCEPTANCE_20261002.json` 及 `docs/ROUND6_FINAL_SOURCE_AUDIT_20261002.json`。下方readiness字段中的API0/未测状态反映冻结时工具验证，不是当前运行状态；本题集不是官方榜单。

本目录只有公开元数据、工具说明和历史草稿；未见问句、reference SQL、完整答案均在外部隔离目录。实施者与根代理只读取本目录及外部 READINESS；不要打开 `system_input.json`、`private` 或预览实际题目。

## 来源与边界

| 项目 | 已验证事实 |
|---|---|
| 上游 | [jOOQ/sakila](https://github.com/jOOQ/sakila)，commit `e089a5b1ec9af0df7a9c6a5d47d49fa1736a4e84` |
| 发行形式 | 上游原生 SQLite schema/insert SQL，原文件逐字节保存 |
| 数据 | 16 表、46,273 行；完整性 ok、外键违规 0；磁盘和内存两次独立还原的完整 profile 一致 |
| 许可 | 原件声明 BSD，具体 variant 未指定 |
| 数据性质 | 上游公开虚构示例，评测工具未生成业务记录；本题集为自编冻结评测，不是官方题目或榜单成绩 |
| 还原边界 | 导入时暂时移除 30 个 audit trigger，保留原 insert 日期，再恢复定义；题目不使用 `last_update` |
| 既有曝光 | 项目此前未用此来源；不声称基础模型从未见过公开 Sakila |

来源目录：`D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002`。
正式 DB：该目录的 `validated/sakila.sqlite`。来源、资产 URL/SHA、许可原文位置、表 profile 见该目录 `SOURCE_MANIFEST.json`。

## 固定题量

| 分层 | 数量 |
|---|---:|
| 独立问数 | 36 |
| 业务域 | catalog / customer_finance / rental_inventory，各 12 |
| 六类主难度 | complex_aggregate / nested_set / multi_table / missing_values / complete_result / time_semantics，各 6 |
| 连续会话 | 4 段，每段 5 轮，20 turn |
| 主分母 | 56 turn，未运行保留失败 |
| 独立题完整结果超过 100 行 | 14 |
| 独立题零行结果 | 0 |
| 独立题含 NULL 的结果 | 5，包括单行 NULL 聚合 |

可解题的 unsupported、clarification、异常及截断均失败。五轮使用真实持久历史，失败后仍继续；整段通过为五轮 AND。既有 AdventureWorks 六题另列开发回归。

评分同时检查真实模型/API 审计、物理输出角色、原 SQL 只读重放与实际返回一致、完整行列、必需读源、真实历史、旧主题过滤及 payload 隔离。保留 SQL 原 LIMIT 和产品 100 行/5 秒上限，不补 reference 行。无序结果比较重复行多重集；顺序题严格比较顺序。整数、NULL 精确；money 用整数 cents reference，绝对容差 `1e-7` 且 cents 精度一致；real 绝对容差 `1e-6`，无相对容差。

## v2 与验证证据

冻结后、首次模型执行前的额外来源审计发现 3 部影片没有演员关联。v2 对 `cs-r06` 的可选关联作语义修正，避免遗漏事实行。仅 reference/答案变化；56 个问题、顺序、标签、配额和分母不变。v1 所有原文件字节保留，v2 公开 manifest 记录父版本及修订工具 hash。

权威状态为外部 `evaluation/v2/READINESS_WORKERS_V1.json`。PUBLIC_MANIFEST 冻结时的 `baseline_ready=false` 保留，READINESS 为放行 sidecar。原 v1/v2 READINESS 均保留，新工具仅使用 workers-v1 sidecar。

v2 reference 独立只读执行两遍共 112 次；readiness 再重放 56 条，47 项评分及并发契约检查通过，包括重复/NULL/精度/顺序/全列、另一合法 SQL、截断、规则 fallback、API 异常、未来问题/Gold 隔离、源码/来源指纹、空观察保留 56 失败与四段五轮，以及 1/2/3 worker 全题唯一分配、会话保序、三个同时线程客户端/ledger/审计隔离、单 worker 异常保留其他结果。验证器禁止网络、不导入 backend。这些是工具验证，不是模型成绩或独立人工语义评审。

`--workers` 支持 1/2/3，默认 1。独立问题和完整 session 单元按 turn 数分配；每 worker 独立持有 provider/client/ledger/engine/KnowledgeStore/ConversationStore，会话内部严格按原顺序。各 worker 实时写独立观察文件，结束后按冻结题目顺序合并。认证或完整性失败广播停止，已在途调用完成留痕；剩余题保留 not_run 和 56 分母。

## 命令

在对应 implementation checkout 根目录执行，Python 3.12 可用。默认命令不读凭据、不发 API、不打开 reference，仅输出数量、hash 与 ready 状态：

```powershell
python -B tools/run_cross_schema_round5.py --workers 3
```

重新验证离线合同，已有 READINESS 后不用 `--write-readiness`：

```powershell
python -B tools/validate_cross_schema_round5.py
```

获得真实模型运行授权后，使用项目本地排除的既有配置。基线和新实现用同组六个工具、同外部 v2 包，分别在各自 checkout 根目录运行、各用新目录：

```powershell
python -B tools/run_cross_schema_round5.py --run-model --workers 3 --output-directory D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/REPLACE_WITH_NEW_RUN_ID
python -B tools/score_cross_schema_round5.py --run-directory D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/REPLACE_WITH_NEW_RUN_ID
```

已有输出目录拒绝覆盖。评分为独立进程，CLI 不开放规则模式成绩。运行只打印 case id、status、API 数量和耗时；完整观察保存在外部 run 目录。401/403、来源/实现变化中止仍保留 56 分母；正常答题失败继续。

`acquire_sakila_round5.py`、`revise_cross_schema_round5_reference.py` 是已执行的来源/修订复现工具，不要对现有目录重跑。本次仅新增独立工具与本目录文件，没有修改 backend、既有评测器或 prototypes。

公开指纹见 `READY_METADATA.json`；`DESIGN_DRAFT.md`、`SOURCE_MANIFEST_DRAFT.json`、`TOOLING_DRAFT.md` 是初始方案历史，不代表当前 readiness。
