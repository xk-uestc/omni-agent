# M1-A 实验记录（2026-10-10）

所有实验使用源码基准 a4ed8bb…，运行时 HEAD 为审计提交 `f5dba53602b806acf27ebbdcf810bd7d919ad0d1`；新 evaluator/input 尚未提交但执行前 SHA 冻结，随本次结果提交一起提供。不使用生产数据/QiMem 数据库/外部模型，不改变 backend。日期使用 Asia/Shanghai。

| 实验 | 实际结果 | 原始记录 |
|---|---|---|
| 185 回合 No Memory 正确性 | 177/185，DB/source 前后相同；无时延重复测量 | `runs/context-no-memory-20261010.json`、`context-stdout.txt` |
| 历史源 hash 对比 | 21 文件不同，不能把历史 185/185 沿用至当前 HEAD | `runs/context-diagnostic.json` |
| 已记录依赖版本有限复现 | 14 探针含原失败会话前置，6/14，8 个失败全部仍失败；非完整重跑 | `runs/context-pinned-dependency-probes.json` |
| 16 个冻结 memory-sensitive A | 10/16，语义 0/4、方法/变更 0/2、控制 10/10 | `runs/m1a-memory-sensitive-no-memory-20261010/summary.json` 与每 task JSON |
| method 前置实际工具验证 | 1 次 DependencyAgent 真实执行 + 独立 SQL/文档/公式评分通过；未注入目标 | 同目录 `preparation.json`、`candidate-evidence.json` |
| scorer 反例 | 首次脚本无效变异 5/6；改 parameters 后 6/6；最终可复跑 CLI 6/6 | 同目录 `scorer-negative-checks*.json` |
| 初次合并现有回归 | 77 个源码用例后首个 HTTP 请求停滞，人工中断 exit 130；不宣称完整通过 | `runs/selected-regression-interrupted-stdout.txt` |
| 源码现有回归独立执行 | 77/77，1.66s | `runs/source-regression.xml`、`source-regression-stdout.txt` |
| pinned HTTP/SSE sandbox | 首个 health 请求停滞，中断 exit 130 | `runs/http-sse-sandbox-interrupted-stdout.txt` |
| 同一 pinned HTTP/SSE 环境外执行 | 14/14，0.76s，3 条弃用 warning | `runs/http-sse-escalated.xml`、`http-sse-escalated-stdout.txt` |
| Fixed Memory B / Oracle C | not_run：Memory Adapter 未实现 | summary 的 comparison_groups |
| 真实远程模型基线 | not_run：本轮无已批准 endpoint/模型/付费预算，未创建 remote provider | summary 的 real_model_baseline |

输入/scorer/fixture hash 全量在开发集 manifest 和 summary；数据库 SHA、原件/别名/源码版本前后、Python/SQLite、34 包锁、任务与 session ID、逐题响应/trace/SQL/参数/来源/耗时都有原始证据。模型调用/token 为 0；trace 工具计数不等于底层数据库调用次数。没有速度增益/官方成绩/已训练结论。

## 确切计分与诊断命令

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_context_semantics_20261009.py --label memory-m1a-no-memory-20261010 --output docs/memory_rl/runs/context-no-memory-20261010.json --skip-latency
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_sensitive_m1a.py --label m1a-memory-sensitive-no-memory-20261010
/tmp/omni-m1a-venv/bin/python tools/verify_memory_sensitive_m1a_scoring.py --output-name scorer-negative-checks-final.json
/tmp/omni-m1a-pinned-venv/bin/python tools/diagnose_memory_m1a_baseline.py
```

测试命令（设置数据库至 /tmp seed，不触碰已提交数据；所有远程 provider 关闭）：

```bash
ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/selected-regression.xml

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py --junitxml=docs/memory_rl/runs/source-regression.xml

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-pinned-venv/bin/python -m pytest -vv ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/http-sse-pinned.xml

timeout 45s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-pinned-venv/bin/python -m pytest -q ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/http-sse-escalated.xml
```

前三条测试命令按上述实际状态记录（合并/HTTP 被中断，所以相应 XML 不存在，不伪造）；第四条通过 require_escalated 执行后实际完成。对照说明 sandbox 环境相关性，不把未经堆栈诊断的 socket 根因写成已确认。

## M1-B1 检查点 1

同一 /tmp/omni-m1a-venv（requirements-initial.txt）执行先写测试：`python -m pytest -q ict-track8/tests/test_memory_core.py`，初始未实现 collection error，记录 runs/m1b1/core-before.txt。核心第一次16/16，增加服务端配置/初始化降级后17/17，分别保留 core.xml/txt、core-final.xml/txt。不改变任何 M1-A 冻结文件。

## M1-B1 检查点 2

先写 Adapter 测试（adapter-before.txt：缺少模块），首次32通过/2失败（合法 channel 无显式 alias，被误判 invalid_filter），修正为当前物理值索引核验后34通过。attempt2 重复失败是 shell 无 `python` 命令导致编辑未执行；实际用 python3 修正，attempt3通过。扩展测试最初19通过/1失败：原 model contract 忽略额外 sql 字段并从合法 plan 构建 SELECT，不能把其误断为 rules_fallback；改为真实非法物理字段后验证原 fallback。旧失败证据全部保留。

```bash
/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_core.py ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py --junitxml=docs/memory_rl/runs/m1b1/source-final.xml
```

实际 stdout：`114 passed in 4.40s`，包括37新增与77旧源码测试。包含重开SQLite、两个独立session、新术语/年份、16并发查询、来源/Schema变更、历史超过8轮仍保存来源收据、模型 SQL 验证和非法字段 fallback。模型为确定性测试 provider，不是远程模型成绩。

API 首次 api-final.txt 为测试跨模块导入 collection error；修正包内相对导入后使用下面同配置重跑 api-final2。API 必须环境外执行（M1-A 已记录 sandbox TestClient 停滞）。

```bash
timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_api.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/m1b1/api-final2.xml
```

API 实际 stdout：`16 passed, 3 warnings in 2.23s`；客户端伪造 scope/memory_enabled 字段不能改变服务端作用域/开关，HTTP/SSE各一次recall/observe。新增 source_versions 与 observe_ms 仅补充事件审计/计时，后续统一复跑记录于检查点3。

## M1-B1 历史约束加固（检查点 2 后发现并修复）

第一轮同源码 A/B (`730370052af66b69ac4ff9f04945f29cd1f48ab4`) 命令：

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label m1b1-ab-20261010
```

stdout 保存在 `runs/m1b1/ab-stdout.txt`；完整产物 `runs/m1b1-ab-20261010/`。16题 A10/B14、语义A0/B4；185题 A177/B177，源码和数据库前后一致。此时新增独立历史反例3项失败，见 `history-adversarial-before.txt`，因此不能把第一轮视作最终交付版本。

实际失败：已消费记忆后追问修改渠道可沿历史改变定义；新增同名冲突候选未在不含术语的追问中再检查；顶层模型失败被原规则成功回退后，事件未记录模型失败事实。修复：沿收据重新检查同名合法候选冲突和当前明确条件；独立 `model_planning_failed` 标记保留成功执行状态，两者不混淆；SQLite Store 连接用 finally 显式关闭。

```bash
/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py --junitxml=docs/memory_rl/runs/m1b1/history-adversarial-after.xml
```

实际输出：`40 passed in 3.48s`。原3项失败转通过。第一轮所有记录保留，后续更换label执行最终完整A/B，不覆盖证据。

补充 Git 环境诊断：第二检查点首次 commit 因缺少 author identity 退出128；沿用此前 `Codex <codex@openai.com>`，使用命令局部 `git -c user.name=Codex -c user.email=codex@openai.com commit ...` 完成，未改全局配置。

## M1-B1 最终版本：同源码完整重跑

源码 `84979a643cc4db7a7a993c80272036219b476192`（历史约束修复后）。依赖继续使用M1-A `/tmp/omni-m1a-venv`，完整版本锁在最终metadata；rules-only、外部模型/Token=0。确切命令：

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label m1b1-ab-final-20261010 > docs/memory_rl/runs/m1b1/ab-final-stdout.txt 2>&1

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_api.py ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/m1b1/regression-hardened.xml > docs/memory_rl/runs/m1b1/regression-hardened.txt 2>&1

/tmp/omni-m1a-venv/bin/python tools/verify_memory_m1b1_evidence.py --label m1b1-ab-final-20261010 > docs/memory_rl/runs/m1b1/verification-stdout.txt 2>&1
```

API测试命令环境外执行（与M1-A一致），实际`133 passed, 3 warnings in 10.70s`。评测stdout逐题/每25回合记录进度，最终输出16题A10/B14、185题A177/B177、stable=true、read_only=true。详见原样stdout，未把未运行项写成通过。

验证工具实际输出以下全部true：frozen_inputs_and_scorers_unchanged、backend_matches_evaluated_hashes、source_stable_during_run、database_read_only、database_matches_m1a、A_pass_vector_matches_m1a、context_pass_vectors_match_m1a、context_off_on_sql_rows_status_equal。9项scorer+消费正反例符合预期。

最终输入manifest SHA256仍为`0b81fde89bd91604e4c5e970c35af7d9f5560723436506bc17c275f8a2c1c4e6`。XLSX跨轮SHA不相同：真实zip逐成员诊断仅docProps/core.xml时间不同，工作表完全相同；最终A/B共享文档实例，无该差异。未修改原冻结文件。诊断XML/hash与存储字节实测在`verification.json`。

主要结果、逐题表、开销、失败边界与not_run原因集中在`M1_B1_RESULTS.md`。保留首轮和最终两套记录，未改任何M1-A历史记录；只更新本目录持续状态文档。最后提交仅文档/核验工具/产物，不改变已评测backend。

## M1-B2 起点与提取契约

fetch实测远程memory=`4e339b6983b13af6891d14a53e51fa9c46b47e2a`，main=`ac69d6e2f7a4a0fe7707e6dee9bab91a52078f7a`。本地仅`?? Qimem/`，保留`Qimem/源代码.zip`。

`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_extraction.py` → `3 passed in 0.04s`（runs/m1b2/extraction.txt）。先实现纯提取器与22题冻结输入；尚未晋升/消费，不宣称形成收益。原rich数据库探针确认SUM销售额、AVG单价/采购周期、COUNT订单规则可执行；不变更原planner。
