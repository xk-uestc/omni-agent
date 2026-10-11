# M1-A 实验记录（2026-10-10）

2026-10-11 真实PDF升级：fetch确认02741bc起点；数据恢复/冻结15422888。按相同原件实跑native+RapidOCR、MinerU4 CPU ONNX、Docling2.137 CPU；开发集证明结构补充的生产RAG LCS收益 .2836→.3545（18题、Top4、3改善0退步），HTTP真实论文取得2表115cell3独立公式，未审核计算拒绝。定向67通过，全部生成模型指标not_run。命令：`tools/run_pdf_parser_trial.py --backend native|mineru|docling`、`tools/evaluate_pdf_parser_trial.py`、`tools/evaluate_real_document_retrieval.py --native-ocr --structured-backend mineru`、`tools/verify_structured_pdf_integration.py`。原始资产/预测/runtime日志不入Git，逐题与SHA收据 `docs/foundation/runs/real_document/`；实现与代价见两份REAL_DOCUMENT文档。保留集及最终回归尚未运行，PPO/GRPO/main/QiMem不变。

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

## M1-B2 检查点2：生命周期

```bash
/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_formation.py ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_extraction.py --junitxml=docs/memory_rl/runs/m1b2/lifecycle.xml
```

实际52 passed in 1.49s（lifecycle.txt）。初始26 passed in 0.65s也保留formation-attempt1.txt。加入候选digest复算、来源新版本重审、superseded关联、冲突和旧Schema测试后通过。此处端到端为单测证据，完整冻结ABC与进程级CLI评测尚未执行。

## M1-B2 首轮真实形成与独立进程复用

`/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label m1b2-formation-first-20261010`，完整stdout=`runs/m1b2/abc-first.txt`。源码e87c156ed887856db1229e6958311ee18d1021d9，正式执行前检查冻结manifest。

实测A9/22、B22/22、C22/22；跨会话目标A0/12、B/C12/12，全部消费且目标历史0。n08单独历史失效控制有1轮先置，未混作跨会话收益。B/C实际binding与rows逐题一致。23候选全部精确对应来源契约（含正确保留的缺失字段），验证20/23，晋升19，错误晋升0；2冲突+1缺binding被拒，1valid候选按任务未审批。n10重复处理同来源保持单候选、多事件关系。

C的MemoryStore.put在父评测进程被禁止；管理员验证/审核由实际`python -m backend.memory.admin`子进程执行，每条命令/exit/stdout/耗时保存在逐题JSON。p01目标另起真实Python进程，其他题重建Store/Engine/ConversationStore。模型与Token调用均0。该“Learned-from-Experience”仅指实际事件形成，不是参数训练。

首轮是实现形成链路的检查点证据。Profile开始后不并行运行测试/评测；若后续优化生产代码，最终状态重跑ABC及16/185。

## M1-B2 专项Profile（优化前）

`/tmp/omni-m1a-venv/bin/python tools/profile_memory_m1b2.py --label m1b2-profile-before-20261010 --repeats 5`，串行100样本：0/16/128条记忆、冷/暖、新术语命中/无命中，每组5次交替off/on配对；20份授权文档。Profile期间无其他agent评测/测试作业；主机外部负载无法控制，load约3.61→3.98。完整分阶段耗时与调用次数在profile.json；阶段为inclusive，不可相加。

发现每请求无命中也扫描20原件（SHA约3ms），命中两次快照40次；Store128条扫描约3ms；主要成本仍为规则/canonical规划，命中规划合计约214ms。prepare中另外约9ms用于1069个value逐个编译/匹配正则。补充7次微测5个问题：literal substring预筛与原span完全相同，约9.2→0.05–0.07ms。该微测与测试有重叠，仅定位算法热点，不做稳定总耗时主张。

计划仅局部优化该等价预筛；不缓存跨请求候选、不跳过来源检查、不动SQL安全逻辑。性能修复独立提交，再做相同脚本100样本Profile及同源码ABC/16/185完整重跑。

## 局部性能修复检查点

仅改变`MemoryAdapter.prepare`保护实体值span的等价预筛：先判断字面值是否出现在question，再编译/匹配正则。没有新增缓存，没有减少SHA/Schema/时态检查。优化前100样本Profile和1069个值的7次微测已记录。

`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_extraction.py ict-track8/tests/test_memory_formation.py`，stdout保存profile-fix-tests.txt；54项通过。将该性能修复单独提交，随后同脚本Profile和新旧全集对照在新源码SHA上重跑。

上一检查点完整测试命令使用M1-B1相同env与API环境外方式，选定原8个测试文件加test_memory_extraction.py、test_memory_formation.py、test_memory_formation_api.py，输出`148 passed, 3 warnings in 3.67s`，见full-before-profile-fix.txt/xml。最终完整确切命令将在最终验证节记录。

## M1-B2 最终源码与全部实际命令

生产源码固定`0a8f97d64d6760e179e3720e309c461d26580fc1`；之后只更改文档、核验工具和harness计时字段。正式输入/scorer保持首次冻结hash。以下命令实际执行，输出重定向原样保留：

```bash
/tmp/omni-m1a-venv/bin/python tools/profile_memory_m1b2.py --label m1b2-profile-after-20261010 --repeats 5 > docs/memory_rl/runs/m1b2/profile-after.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label m1b2-formation-final-20261010 > docs/memory_rl/runs/m1b2/abc-final.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label m1b2-legacy-final-20261010 > docs/memory_rl/runs/m1b2/legacy-final.txt 2>&1

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_api.py ict-track8/tests/test_memory_extraction.py ict-track8/tests/test_memory_formation.py ict-track8/tests/test_memory_formation_api.py ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/m1b2/final-tests.xml > docs/memory_rl/runs/m1b2/final-tests.txt 2>&1

/tmp/omni-m1a-venv/bin/python tools/diagnose_memory_experience_m1b2.py > docs/memory_rl/runs/m1b2/cross-source-probe.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_memory_m1b1_evidence.py --label m1b2-legacy-final-20261010 > docs/memory_rl/runs/m1b2/legacy-verification.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_memory_formation_m1b2.py --label m1b2-formation-final-20261010 > docs/memory_rl/runs/m1b2/formation-verification.txt 2>&1
```

实际结果：148 passed, 3 warnings in 14.32s（API用require_escalated运行，与已知sandbox限制保持一致）；原16 A10/B14，185 A177/B177，源码/数据库前后相同；跨源probe `baseline_statuses=[clarification,clarification,clarification], legal_plan_passed=3,total=3,database_unchanged=true`。原回归独立核验所有hash、pass向量、A/B SQL/rows/status检查true。

Profile前后各100个样本串行；期间未并行运行任何agent评测/测试。机器/CPU数/负载与源码hash在profile.json；脚本及分位数定义不变。5样本/条件的lower经验P95不能当稳定尾延迟估计。source SHA调用次数前后一致；没有通过跳过校验换取时延下降。

补全harness的真实end_to_end_ms（资料入库前→目标返回，含CLI/Store重建）后再次执行：

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label m1b2-formation-final2-20261010 > docs/memory_rl/runs/m1b2/abc-final2.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_memory_formation_m1b2.py --label m1b2-formation-final2-20261010 > docs/memory_rl/runs/m1b2/formation-verification2.txt 2>&1
```

final2为最终22题记录，之前first/final全部保留。生产源码/输入/Gold/独立scorer均未变；runner版本hash各自记录，不回写旧report。最终A9/B22/C22、正向0/12/12、验证20/23、晋升19、错误晋升0；候选精度独立定义20/23、字面保真23/23。C全链总19079.328ms、P50 891.911ms、P95 1066.351ms；目标问答总6289.418ms。6个scorer/消费正反例全部符合预期；12条正向形成链路的event/candidate/CLI/source/SQL/consume证据核验全部true。BC知识binding/rows、原16/185与M1-B1逐题核验true。

模型/token调用0；真实远程模型not_run，无已批准配置/预算。本轮没有训练、skill演化或跨源经验Adapter实现。失败/边界：新A的13题（12未知术语+n10）仍失败；3个非法/不完整候选验证拒绝；旧8例与f01/f02仍失败；POSIX本地管理员边界、结构化TXT提取、同步存储/重复规划成本、保留策略和外部负载限制均明确保留。

最终交付核验：artifact-sha256.json列出的274个产物/工具Hash全部匹配；新旧冻结manifest无差异；已评测0a8f97d后的backend无改动。提交前再次fetch：origin/memory仍为0a8f97d64d6760e179e3720e309c461d26580fc1，origin/main仍为ac69d6e2f7a4a0fe7707e6dee9bab91a52078f7a。暂存`git diff --cached --check`报告final-tests.txt中6处pytest弃用warning原文的尾随空白；保留原始stdout及其Hash，不将该检查声称为无告警。其余非运行产物的diff检查通过，暂存文件不含QiMem原始资产、SQLite运行库或.env。

## M1-C 检查点1（2026-10-10）

fetch核验HEAD/origin memory=5a5f14f8c5aeab0aa9ce331da8b628bcd98933e0，main未变。只有未跟踪Qimem/，保留。
实际命令：`/tmp/omni-m1a-venv/bin/python tools/diagnose_memory_m1c.py > docs/memory_rl/runs/m1c/diagnosis.txt 2>&1`。四类执行完成，但runner忘建输出目录，FileNotFoundError退出1；保留stdout。修正输出目录后以新label执行：`/tmp/omni-m1a-venv/bin/python tools/diagnose_memory_m1c.py --label m1c-diagnosis-complete-20261010 > docs/memory_rl/runs/m1c/diagnosis-complete.txt 2>&1`，退出0。原问题完整约束下A/B ok、C incomplete/source_dynamic_binding_unverified、D clarification/source_scope_unverified；四类无模型Agent均clarification。真实模型not_run，等待用户免费本地部署入口及预算，不发付费请求。

M1-C类型契约测试命令：`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_task_experience.py > docs/memory_rl/runs/m1c/experience-tests-first.txt 2>&1` → 11 passed in 0.93s。共享治理回归：`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_formation.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_extraction.py > docs/memory_rl/runs/m1c/shared-governance-tests.txt 2>&1` → 54 passed in 1.55s。

## M1-C 真实模型预检与接入

用户提供本地私密配置并先授权8次DeepSeek请求。实际命令：`/tmp/omni-m1a-venv/bin/python tools/probe_memory_m1c_model.py --config runtime/private/m1c-model.env --max-calls 8 --label m1c-model-preflight-20261010 > docs/memory_rl/runs/m1c/model-preflight.txt 2>&1`。5次请求（B含一次修复），HTTP均200；A/B工具执行ok，C/D来源绑定失败。B最终答案缺华北方法证据，不能称整题成功。请求deepseek-chat，实际返回deepseek-flash，audit.model_verified=false如实保留；正式评测将显式固定deepseek-flash。输入20429、输出955 tokens（精确usage在逐调用JSON），零传输重试。secret文件600、runtime被ignore，原始请求不含Authorization。

用户随后明确批准本轮累计最多144次请求，包含已用5次，temperature=0、每次输出≤5000。本地配置改为明确deepseek-flash，持久ledger不重置；正式任务计划为开发/保留各8题×四组，至多一次计划修复，余量仅接入预检。未使用预算不消耗。

接入测试首次105项中1失败：Replay test使用json.dumps默认ASCII转义后替换中文，地区未变，原约束正确拒绝；不是系统新错误。保留planner-integration-tests.txt、failure-detail。修正测试序列化ensure_ascii=False后105 passed in 3.57s；追加事件观察与模型预算测试后15 passed in 1.24s（experience-integration-final.txt）。mock结果只证明机制，不作为模型成绩。

## M1-C 冻结前机制核验

新增typed formula_labels适用条件，避免“公式”泛相似就选择不同指标方法；agent_execution形成要求真实模型名称核验与原问题/实际图digest收据，继承独立执行重验与本地审核。选择不含最终值/历史SQL。

种子独立预检初次A/B被评分器误拒：in-memory RANGE值是tuple而JSON是list，评分器冻结前规范化为list；保留precheck.txt，尚未正式评测。修正后A/B完整独立核验通过；客单价developer图仍source_scope_unverified，保留为未形成种子，不放松原来源规则。种子及目标采用已测试文档公式语法，全部在冻结前定义。目标Gold只在所有执行完成后读取。16题开发/保留各8；正式运行后不根据保留集改输入/Gold/scorer。

首次正式label `m1c-formal-20261010`在开发阶段中止，已完成d01–d03共12条，保留全部原始输出与INTERRUPTED.json，不计算正式总成绩。原因：显式deepseek-flash默认thinking enabled，temperature=0实际上不生效；一次JSONDecodeError，原transport未保存无法解析的assistant正文，仅有usage/error_type，因此不能事后推断该内容。官方API参数说明已核对。主动停止自己的进程，累计ledger22次（含预检5、种子2、开发及修复请求），其中在途终止可能计费、Token未知，不能填0。

随后明确请求thinking.disabled，增加有界assistant_content失败证据、audit解码字段；新model_run_v2.json独立补充固定运行配置，原输入/Gold/scorer/manifest均不变，不覆盖原运行。顺带发现experience新import会使默认服务在非POSIX因pwd导入失败：将pwd延迟到本地管理员入口，非POSIX审批明确fail closed，默认服务模块可导入；不宣称Windows全栈已验证。修复后机制/预算/独立评分17项通过（transport-fixed-tests.txt）。正式重跑仍受累计144额度约束，不重置ledger。

## M1-C 最终同源码真实四组与回归

生产源码`91d7633a23956b27710599c933913ac79c6022de`。正式命令（无自动付费重试）：

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_task_experience_m1c.py --config runtime/private/m1c-model.env --max-calls 144 --label m1c-formal-final-20261010 > docs/memory_rl/runs/m1c/formal-final.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_task_experience_m1c.py > docs/memory_rl/runs/m1c/verification.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label m1c-legacy-regression-20261010 > docs/memory_rl/runs/m1c/legacy-regression.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label m1c-formation-regression-20261010 > docs/memory_rl/runs/m1c/formation-regression.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_memory_m1b1_evidence.py --label m1c-legacy-regression-20261010 > docs/memory_rl/runs/m1c/legacy-verification.txt 2>&1
/tmp/omni-m1a-venv/bin/python tools/verify_memory_formation_m1b2.py --label m1c-formation-regression-20261010 > docs/memory_rl/runs/m1c/formation-verification.txt 2>&1
```

四组16题结果：A7/B7/C8/D9；开发4/5/4/5，保留3/2/4/4。固定经验无成功率增益。B/C所有16对首请求context及选择相同，不能将1题差异解释为Oracle策略收益。正式种子A在JSON协议解析失败，不确认；B真实Agent→独立重执行/整题核验→本地CLI审批成功，正式池仅1项；客单价种子来源范围失败。B/C只在d03/d04/h03/h04选择同一B经验。直接保留模型非法JSON/DSML输出，未写宽松解析绕过协议。

最终64任务均新会话history=0，源码/DB前后Hash相同。输入manifest保持ed8972c1e24cc691ade7bb7b54f7f8492d4e72104576c361f5f166190b821992。正式目标请求71次，种子3次；累计ledger96（含预检与中止轮），上限144。95次有返回audit；中止时第22次在途响应未知，可能计费，Token不能填0。已知输入432293、输出35477；金额未独立核对，实际账单为准。剩余额度48未使用。

业务回归原16 A10/B14、185 A177/B177，逐题pass和SQL/rows/status匹配原记录；B2 22题9/22、22/22、22/22，正向0/12/12、候选23验证20晋升19，源码/DB稳定。两项独立核验工具均退出0。

选定完整测试确切命令：

```bash
timeout 180s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_api.py ict-track8/tests/test_memory_extraction.py ict-track8/tests/test_memory_formation.py ict-track8/tests/test_memory_formation_api.py ict-track8/tests/test_task_experience.py ict-track8/tests/test_task_experience_api.py ict-track8/tests/test_m1c_model_budget.py ict-track8/tests/test_m1c_scorer.py ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py ict-track8/tests/test_fusion_literal_protocol_round4.py ict-track8/tests/test_sql_document_binding.py ict-track8/tests/test_omni_plan_recovery_round4.py ict-track8/tests/test_fusion_source_constraints.py ict-track8/tests/test_fusion_history.py ict-track8/tests/test_omni_agent.py --junitxml=docs/memory_rl/runs/m1c/final-tests.xml > docs/memory_rl/runs/m1c/final-tests.txt 2>&1
```

实际412 passed、3 failed、3 warnings，15.79s，不写全绿。三项失败test_metric_display_alias_is_bound_to_same_physical_sum、test_new_topic_and_reset_do_not_inherit_sources、test_model_sees_verified_sql_followup_before_route_selection_and_repairs_spurious_clarification。
用`git archive 5a5f14f8c5aeab0aa9ce331da8b628bcd98933e0 ict-track8/backend ict-track8/tests ict-track8/data`导出起点源码到/tmp/omni-m1c-start-snapshot（不切换/修改任何分支）。最初指定不存在的ict-track8/config导致archive退出128，移除不存在路径后成功。

```bash
/tmp/omni-m1a-venv/bin/python -m pytest -q /tmp/omni-m1c-start-snapshot/ict-track8/tests/test_sql_document_binding.py::test_metric_display_alias_is_bound_to_same_physical_sum /tmp/omni-m1c-start-snapshot/ict-track8/tests/test_fusion_history.py::test_new_topic_and_reset_do_not_inherit_sources /tmp/omni-m1c-start-snapshot/ict-track8/tests/test_omni_agent.py::test_model_sees_verified_sql_followup_before_route_selection_and_repairs_spurious_clarification > docs/memory_rl/runs/m1c/start-snapshot-failure-check.txt 2>&1
```

三项相同失败在起点0.78s原样复现，非本轮新增；保留输出。主模型计时期间没有并行跑本Agent测试/回归；旧回归期间有一次起点三失败诊断，不将旧回归wall作为本轮性能因果证据。

补充真实model-formed B条目的治理测试无付费调用：当前可选1条→methods原件改变拒用、显式本地撤销拒用、换project拒用，3/3；记录在最终verification.json。d08/h08正式公式控制因没有形成公式种子而是空池控制，不能冒充真实撤销成功。h07冻结评分仅证明未发布无依据数值，实际泛化澄清没有正确解释缺原件原因。C检索字段只计第二次选择pass，第一次oracle候选扫描耗时未单独保存，故只可报告该阶段下界；完整wall包括两次。D为审计运行相同检索扫描但不向模型提供经验，实际输入Token并非严格相等。

## F1 起点审计（2026-10-10）

`git fetch origin memory` 沙箱只读.git失败，授权提升权限后成功，remote=94a9926de2cee14c014a9c6e93c63112407d7760。M1-C未提交报告保留后独立归档16c5d28729acfb0ebba7b8930426b2a3241bf427。仅历史JSON/hash审计，0模型请求。

`env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_knowledge_store.py ict-track8/tests/test_dense_retrieval.py ict-track8/tests/test_evidence_coverage.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_sql_document_binding.py ict-track8/tests/test_fusion_history.py ict-track8/tests/test_omni_agent.py > docs/foundation/runs/baseline-tests.txt 2>&1`

153通过5失败；2项不存在夹具数据库的测试环境错误、3项已有失败。100文档真实BM25性能见foundation/runs/storage-baseline.json；不计模型耗时。

F1分层开发输入冻结：`/tmp/omni-m1a-venv/bin/python tools/freeze_foundation_rag.py`；manifest冻结输入、协议、生成器、评分脚本SHA。`/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_rag.py --output docs/foundation/runs/rag-A-dev-before.json`：24题源与必要证据Recall1，21/21 annotation complete，无关来源.645833，生成模型not_run。保留8题尚未用来优化。

修正测试夹具：通过initialize_database('/tmp/f1-api.sqlite')建立隔离样本后，原定向命令重跑，日志baseline-tests-configured.txt；原环境失败日志保留。

`/tmp/omni-m1a-venv/bin/python tools/fetch_foundation_bge.py`（允许网络提升权限）：7项资产核验，下载95,827,648 bytes固定权重。沙箱首次网络socket拒绝后同命令提升成功；无凭据输出。`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 /tmp/f1-bge-venv/bin/python tools/evaluate_foundation_rag.py --dense --output docs/foundation/runs/rag-B-dev-before.json > docs/foundation/runs/rag-B-dev-before-stdout.txt 2>&1`：BGE本地真实执行6/24，英文18项跳过，不生成答案。

F1检查点3：`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_retrieval.py ict-track8/tests/test_dense_retrieval.py > docs/foundation/runs/retrieval-final-tests.txt 2>&1`；`/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_navigation.py --strategy hierarchical --output docs/foundation/runs/rag-C-prototype-dev.json`。未采纳C生产架构，原型保留。原草案C/BGE开发实验均保留json/stdout；相同候选预算。

`/tmp/omni-m1a-venv/bin/python tools/measure_foundation_storage.py --output docs/foundation/runs/storage-after.json`：100doc/300chunk同规格热请求约4.00→1.87ms，实际SQL下推3chunk，非模型问答。FastAPI TestClient在沙箱socket限制下挂起，允许本机socket、外部provider显式为空后跑baseline-tests-configured-network.txt，保留所有失败日志。

检查点4：`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_evidence_scope.py ict-track8/tests/test_source_answer_dossier.py ict-track8/tests/test_native_row_selection.py ict-track8/tests/test_pdf_page_index.py ict-track8/tests/test_evidence_coverage.py ict-track8/tests/test_foundation_retrieval.py > docs/foundation/runs/evidence-scope-tests-final.txt 2>&1`。初次test helper import失败见evidence-scope-tests.txt，改为独立原生PDF生成器后重跑。顺带修复无scope调用records()的旧instrumentation兼容，source scope仍下推。

safe-09独立修复：`env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= timeout 90s /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_safety.py ict-track8/tests/test_api_security.py ict-track8/tests/test_memory_api.py ict-track8/tests/test_task_experience_api.py > docs/foundation/runs/safety-tests-final.txt 2>&1`：26passed、3既有warnings；实际HTTP/SSE及低层SQL整条拒绝，无SQL/检索/模型部分执行，数据库SHA保持。初错HTTP路径日志保留。两项旧测试部分执行断言与新授权契约冲突，未改旧断言/Gold。

F1可靠性：`/tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_failure_trace.py ict-track8/tests/test_sql_document_binding.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_memory_core.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_task_experience.py > docs/foundation/runs/reliability-tests-final.txt 2>&1`。SQL显示alias旧断言保持而修复实现；invalid DAG、缺来源、ref参数绑定、第一模型协议失败、子任务成功但整题未完成分别记录。首轮reliability-tests-first.txt暴露危险请求提前拒绝缺少memory非消费receipt，已补充不执行任何Memory的明确receipt。

`env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= timeout 90s /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_safety.py ict-track8/tests/test_omni_query_stream.py ict-track8/tests/test_omni_plan_recovery_round4.py ict-track8/tests/test_evidence_recovery_round5.py ict-track8/tests/test_document_transport_recovery.py ict-track8/tests/test_fault_recovery.py > docs/foundation/runs/reliability-api-tests.txt 2>&1`（本机socket）。不调用真实模型。

恢复测试两项失败在起点源码复现：`git archive 94a9926de2cee14c014a9c6e93c63112407d7760 ict-track8/backend ict-track8/tests ict-track8/data | tar -x -C /tmp/f1-start-snapshot`；`/tmp/omni-m1a-venv/bin/python -m pytest -q /tmp/f1-start-snapshot/ict-track8/tests/test_evidence_recovery_round5.py::test_real_source_retrieval_recovery_answers_original_question_once /tmp/f1-start-snapshot/ict-track8/tests/test_evidence_recovery_round5.py::test_navigation_sees_original_condition_beyond_retrieval_preview > docs/foundation/runs/recovery-failures-start-reproduced.txt 2>&1`。2failed，原样错误。未改生产去适配错误旧插桩/不含条件的snippet假设。

原M1-C测试+新安全/Trace：首次memory-mechanism-tests.txt 439passed/7failed，5项为未执行工具Trace空契约。修正为独立failure_trace后相同命令输出memory-mechanism-tests-final.txt及xml：444passed/2历史failed，旧断言不变。源码版本固定后才复跑16/185/22与最终性能；安全拒绝结果的新基线不混作Memory增益。

F1检查点6固定源码a37c0728deacf1bc01a6e1458ac3826a2d570e2e：

- `/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label f1-memory-final-20261010 > docs/foundation/runs/memory-16-185-stdout.txt 2>&1`：16题10/14；185题178/178，旧177/177仅整体拒绝1题改善，无退步；源码/DB稳定。
- `/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label f1-formation-final-20261010 > docs/foundation/runs/memory-22-stdout.txt 2>&1`；`tools/verify_memory_formation_m1b2.py --label f1-formation-final-20261010`：22题9/22、22/22、22/22、0错误晋升，独立CLI治理有效。
- `tools/verify_memory_m1b1_evidence.py --label f1-memory-final-20261010`：退出1，原“必须与M1A逐题完全不变”断言因授权安全改善不成立；文件/score不改。`/tmp/omni-m1a-venv/bin/python tools/verify_foundation_final.py > docs/foundation/runs/final-verification-stdout.txt 2>&1`独立验证冻结输入/scorer、整条危险请求拒绝、原成功0退步、开关SQL/rows/status一致，退出0。
- `/tmp/omni-m1a-venv/bin/python tools/measure_foundation_performance.py --output docs/foundation/runs/performance-final.json > docs/foundation/runs/performance-final-stdout.txt 2>&1`：10/100/500页、1k/10k/100k行、4并发、重启、增量、来源改变/锁/缺索引/损坏数据库/模型fake503。正常请求失败0；锁15秒明确失败。无模型生成。
- 最终开发A/B与保留A/B各使用`tools/evaluate_foundation_rag.py --split dev|retained [--dense] --output ...`；B环境HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 /tmp/f1-bge-venv/bin/python。保留旧版用`tools/evaluate_foundation_snapshot.py --snapshot-root /tmp/f1-start-snapshot --source-commit 94a9926de2cee14c014a9c6e93c63112407d7760 --split retained [--dense] --output ...`；C用`tools/evaluate_foundation_navigation.py --strategy hierarchical --split retained --output ...`，仅原型。stdout与json逐次新文件保留。
- 最终相关集合（全部改动+旧NL2SQL/恢复/来源/native、HTTP/SSE）533passed/7failed/12subtests，见foundation-final-tests.txt/xml；所有7项根因分类见SAFETY_AND_RELIABILITY.md。新的AVG/SUM旧失败以起点snapshot原样复现，日志dynamic-failure-start-reproduced.txt。未改旧断言。

最终SSE超时fault：`env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= timeout 30s /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_stream_fault.py > docs/foundation/runs/stream-fault-test.txt 2>&1`：1passed，3既有warnings；真实TestClient stream，超时后台仍活跃且占用semaphore，测试随后释放并回收thread。不冒充取消实现。

最后核对发现独立验证JSON的before/after_chunk_ids曾错误命名为document ID；保留原final-verification.json作为初次审计，更正字段含义并分别记录document/chunk ID，输出final-verification-v2.json与stdout。指标不变，64成对0变化，冻结Hash/源码/DB稳定、Memory0退步。legal-scope-edit.json另保存实际“删除地区限制”进入合法澄清而非危险请求拒绝的本地输出。

检查点6 commit+push完成：e1a815c2cc57bdfc3789d0282da8bd255dde97c2。git whitespace检查排除pytest原始txt/xml产物（原始输出自带空白不清洗），其余源码/报告通过。检查点7最终报告/STATUS/DECISIONS/CODEX_REPORT/ACCEPTANCE只写当前证据和限制，最终资产manifest对全部本轮产物和冻结输入逐文件SHA256核验，不含私有目录、密钥或权重二进制。

## F2 follow-up：manifest重验、困难集A/B复跑与索引profile（2026-10-11）

执行SHA为`d96a2faa79315318dbeed8ccb070cd56b0ed4f5b`，manifest声明生产源码冻结`6608785ac364d4188948e1e4167cfb21c9da5b3d`。manifest的189项文件均匹配SHA256与长度，manifest自身SHA为`cdd391b11df936533ffbaf26435f5687fe17a5a2ebef30692d7d40e8bf5fcf80`。复跑命令和结果见`docs/foundation/F2_FOLLOWUP_REPRO_20261011.md`。

动态BM25与持久BM25在dev/retained的来源Recall、证据Recall、MRR/NDCG、完整必要证据、错误绑定与无证据污染均一致；完整证据仍只有6/21，污染3/3。小语料重复P50约23ms、持久索引约1.73/1.75MB，未见可靠提速。10/100/500/1000页native PDF持久索引热P50为4.89/30.30/525.92/1087.46ms，动态为4.50/106.68/710.71/1452.26ms；建索引为4.97/67.50/342.07/702.90ms。profile 72次暖查询显示tokenizer和page-scope累计时间较高；没有据单次样本改排序/tokenizer。

跨源SQL/目录绑定/编译器/对话修改/round9-round10 fixture回归315 passed。困难RAG集自身是文档QA，不含SQL gold与数据库；答案EM、忠实度、引用正确性仍not_run。这里的A/B是dynamic与persistent BM25实现对照，不是固定记忆与动态记忆的因果对照；证据检索完整度仍不足，因此F2不证明Memory收益，也不启动M2训练。

## F3 检查点1：冻结困难集逐阶段损失归因（2026-10-11）

起点`d61ce23dfa32fcb2be01d6b71235f3e48e693aed`。新工具`tools/diagnose_foundation_f2.py`在读取Gold前运行相同KnowledgeStore解析、BM25 Top-20/排序/Top-4、EvidenceCoverage、native dossier；仅dev的六道表格题继续重放F2 bounded table chain。对必要Gold literals分别记录raw/source、parsed、候选、重排Top-4、最终selector、Dossier及表链页面SHA/locator，并报告first-loss、wrong-binding和unsupported污染。exact scorer parity断言对照冻结F2两split，Gold只作事后诊断。

运行`validate_foundation_f2.py`：48题、30文档、literal Gold及输入hash通过、来源族无泄漏；`test_foundation_f2_evidence.py` 4 passed；诊断工具dev/retained均退出0。每split 99 required literals。exact Top-K宏Recall与冻结结果相同dev`.4325`/retained`.4167`。表格行解析把pipe row转为带标签字段；只按已知字段标签与分隔符做规范化后，dev解析99/99、retained 98/99仍可恢复，exact文本解析Recall为`.7381/.7222`。规范化BM25 Top20宏Recall`.7024/.6865`，重排Top4`.4714/.4595`，EvidenceCoverage最终`.4754/.4595`；selector提升dev`.0040`、retained`0`。TopK+dossier是诊断context`.6984/.6825`（完整12/21、11/21），非答案正确率。first loss literal counts、SQL指标notrun及Gold次序说明见`F3_FAILURE_ATTRIBUTION.md`与`runs/f3-cp1-stage-loss-{dev,retained}-20261011.json`。

F2表任务旧A`.0417`分数从不同来源拼接hit文本后计算。逐题按所需source重算exact宏均值`.0139`：仅一个单位表头来自正确source，另两个单位命中是邻近family表。归档历史值并报告source-bound复核；未改F2 scorer/Gold。six dev table任务field-normalized source-bound evidence A Top-K`.1639`完整0/6、dossier`.6667`完整4/6、bounded chain `1.0`完整6/6。错误来源绑定仍6/24，典型是2025 vs 2026同标题公报与同名异机构林青；unsupported污染3/3。答案器未调用。


F2检查点1：`/tmp/omni-m1a-venv/bin/python tools/freeze_foundation_f2.py`冻结48题；`/tmp/omni-m1a-venv/bin/python tools/validate_foundation_f2.py`验证48题Gold literal、来源族隔离及全部文件SHA；`/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split dev --output docs/foundation/runs/f2-A-dev-before.json`与retained对应命令生成No Change输出。开发探索结果另存`f2-dev-exploration.json`。两split同源码Python3.12.3/SQLite3.45.1、动态BM25、top4，没使用Dense或生成模型。保留baseline是在任何索引方案改变前执行；随后不可用于选参数。

F2检查点2：`ICT8_DB_PATH=/tmp/f2-api.sqlite`等空模型配置，本地pytest `tests/test_retrieval_index.py tests/test_knowledge_store.py tests/test_dense_retrieval.py tests/test_foundation_retrieval.py tests/test_dynamic_source_binding.py`为56 passed、5 warnings。API测试库先以`initialize_database('/tmp/f2-api.sqlite')`建立。索引准确性开发集输出为`f2-B2-dev-after.json`，与No Change相关指标一致；FTS5候选为`f2-C2-fts5-dev-after.json`，Source Recall .6190、Evidence Recall .4286，P50/P95 42.19/92.29ms。Native PDF规模输出`f2-index-scale-after2.json`，10/100/500/1000页动态/索引P50=4.55/4.94、107.41/30.79、715.83/529.24、1461.08/1102.45ms；构建3.65/67.28/343.12/705.61ms；规模控制与索引命中SHA/locator相同。工具不覆盖旧输出、无模型调用。

F2检查点3：`PYTHONPATH=ict-track8:tools ... pytest -q ict-track8/tests/test_foundation_f2_evidence.py`为4 passed；新增测试拒绝预算不足、重复行ID及检索后更新的旧SHA。`evaluate_foundation_f2_evidence.py --split dev`输出`f2-evidence-scope-dev-final-v10.json`：6个表格任务必要证据Recall A/B/C为.0417/.7222/1.0000，完整0/6、4/6、6/6。现有B每题扫描7–15页、供应≤6页且Scope为budget_exhausted；C使用1–2次top4查询并扫描3–4个原生PDF页（<=6总预算），核验30个唯一表行、单位、重复表头、检索实体编号绑定、明确筛选字段、排除脚注和SHA。哈希前后相同。C total局部时延高于B的部分题，不将该离线候选宣称为生产收益或公开成绩。

F2检查点4：相关回归运行`PYTHONPATH=ict-track8:tools ICT8_DB_PATH=/tmp/f2-api.sqlite`及空模型Provider配置，pytest `test_metric_catalog_bindings.py test_metric_compiler.py test_dynamic_source_binding.py test_memory_adapter.py test_conversational_sql_edit.py`：217 passed。独立SQLite查询核验分组AVG、COUNT(DISTINCT)、NULL跳过与组内平均分母；动态来源scope明确AVG在Catalog扩展后仍匹配；显式平均不能沿用历史SUM记忆。根因和覆盖审计规则见`docs/foundation/F2_SEMANTIC_CORRECTNESS.md`。无保留集使用、付费模型调用或冻结Gold/评分器变更。

F2检查点5：`PYTHONPATH=ict-track8:tools ICT8_DB_PATH=/tmp/f2-api.sqlite`，空模型/检索Provider配置，运行 Responses provider/audit、ModelPlan strict contract、既有一次结构修复合同四测及SSE故障：50 passed、2个FastAPI弃用警告。DSML混入JSON夹具确认严格拒绝；运行审计只留输出SHA/字符数及JSON解析位置。M1-C已有正式原始调用产物保留，错误数A/B/C/D=2/4/3/3，未重放或请求真实模型。同步SSE测试直接消费本地路由生成器，worker在超时后继续持有BoundedSemaphore，完成后释放；agent线程启动失败路径亦验证释放。未证明断流强制取消、耐久任务查状态或重试幂等。细节见`docs/foundation/F2_RUNTIME_RELIABILITY.md`。

F2检查点6：生产源码freeze=`6608785ac364d4188948e1e4167cfb21c9da5b3d`；最终合并集合950 passed/6 failed/12 subtests/25 warnings，用仓库文档所需本机socket权限执行，日志`f2-final-tests-v4.txt/xml`。六失败与F1原7项完全重叠，F2 AVG/SUM旧失败现通过；原断言未删改。证据覆盖测试仅改为显式动态BM25，因为它stub的是动态retriever接口；测试通过，生产代码未变。F2 dev/retained最终A/B动态/持久结果分别在`f2-final-{dynamic,persistent}-{dev,retained}.json`，所有召回/排序/错误来源/污染/SHA locator完全相同；小集延迟近似。最终6题表scope A/B/C=0/6、4/6、6/6。F1 32题BM25/BGE同为满召回，BGE无Dense收益且略慢。M1-B1新label输出16题10/16→14/16，185题178/185两组；M1-B2新label22题9/22→22/22→22/22，candidate precision20/23/source fidelity23/23。详细评分/六旧失败分类及成本见`docs/foundation/F2_FINAL_REVIEW.md`；`F2_FINAL_ARTIFACT_MANIFEST.json`包含freeze SHA、提交链、实验输入和最终日志哈希。所有模型Provider空配置，BGE仅本地已固定权重离线运行；没有付费API/训练。
# F3 检查点2：候选检索实验（2026-10-11）

在冻结困难集开发split上运行当前BM25、有界查询分解与本地BGE尝试。BM25逐题基准复核CP1：候选必要证据Recall@20 .5595，最终Recall@4 .4325，完整6/21，错绑6/24，无证据题污染3/3。分解最多扩展到2个短子句，并保留显式标识/年份，每题最多3次查询。14题chunk集合变化，但必要证据Recall、完整数、错误绑定和污染无改善，P50/P95由22.52/61.89升到43.84/120.84ms。没有部署。

更正前述环境说明：`/tmp/omni-m1a-venv`是M1-A/F2轻量复测环境，没有Torch；F1既有命令明确把Dense评测放在`/tmp/f1-bge-venv`。本轮按该流程离线实际运行BGE，并将BM25、分解在相同venv重跑。实际依赖为Torch 2.14.1+cu130、Transformers 5.19.0、safetensors 0.8.0；Transformers超出项目`<5`范围，须标注验证限制。BGE dev结果来源Recall@4 .8571、字段证据Recall@4 .4405、候选证据Recall@20 .5595、完整6/21、错绑6/24、污染3/3、P50/P95 41.77/1396.91ms。增益不足以修复完整证据或来源错误，不上线。没有联网下载、模型API调用或训练。详情：`docs/foundation/F3_RETRIEVAL_RESULTS.md`及`docs/foundation/runs/f3-cp2-*.json`。

## F3 来源条件绑定（2026-10-11）

fetch 确认 memory 本地/远端 `bd666d5efdcedd5167c65136e190136114553a1b`，与指定起点相比仅评测和文档新增。先写5反例4失败，再修改生产 search 和 source_binding。冻结开发集 Recall@4 .4325→.5040，完整6→7/21，错误来源6→0/24，无证据污染3/3保持。详见 `docs/foundation/F3_IMPLEMENTATION.md` / `F3_RESULTS.md` 及逐题原始运行；无付费模型/训练，非公开基准。来源内关联和表链接入继续执行。

F3 第二项：生产 search 接入有明确正文关系的来源内对象范围，开发 Recall@4 .5040→.5754，完整7→10/21，错误来源0/24保持。3道排除条件题改善，0必要证据退步；MRR下降已披露。预算反例揭示重复页眉剔除问题，改用原文页数，最终42项来源/SQL/HTTP知识库回归通过。第一项完整远端SHA bee7f2ef2dd01b4cb6fb8c5c3241b47aa0b91e2f。

F3 第三项：F2受限原页表链生产化到 prepare_evidence/answer/dossier。六题各30行原文/10匹配行，均完整；Top-K .5754/10，Top-K+单独bundle .8571/16，实际answer证据 .7619/15，错绑0/24。0真实模型调用；表格无独立审核器时显式待审核。更新拒用反例1失败后修复为初始hits立即pin校验，最终156定向回归通过；所有原始日志保留。下一步仅冻结后的保留集/最终回归/成本记录。第二项远端SHA efa941c8de8db9a6809a3ae30f633412ef62ff3c。

F3 最终冻结复核：生产及远端 `67c4d3963d7078edeb9c1d5f7c58981caa072ffd`。正式保留Top-K .5595、10/21、错绑3/24；Top-K+bundle .7024/13，实际answer证据 .6071/12。机构语法未识别中心/研究所/监测局，保留3机构表题拒用及3错绑保留，不调规则。F1同flat32题指标逐题不变；16/185/22完全保持F2 pass向量、DB只读、model_calls=0，旧M1-A equality verifier失败及新F2独立核验成功均原样记录。934通过/6既有失败/12子测试，task_experience16测试通过；tracked demo测试副作用已恢复原Git字节。成本同环境P50/P95由23.79/103.39ms增至75.15/159.82ms，最大Python分配峰值+约3.81MiB，既有索引-40960bytes，无新索引结构。正式成绩不是答案准确率/公开benchmark。完整原始命令/日志/哈希在F3_RESULTS及runs/f3，结束本轮并等待ChatGPT审核，不训练、不改main。

## 2026-10-11 真实OHR扫描表生成答案与视觉回放

- 原有真实失败：OHR财务扫描页RapidOCR把Sales/Use Tax总额识别为`$343.215.345`并拆坏表格行/列；完整OmniAgent链路只能返回页级OCR摘录，固定OHR答案scorer EM0、F1 .019802。
- 生产改动：`tools/m1c_model_client.py`复用原PDF视觉证据校验并映射DeepSeek图像输入；`visual_source_answer.py`只对显式opt-in `deepseek-flash`开放原页选择+第二次独立复核，模型选择不授权数字执行。对通过审核的单一金额/数量问题，服务器仅展示唯一完整数字字面量，行标签和表头继续留在引用中；有歧义/独立审核失败仍旧回退OCR摘录。
- 真实完整Agent调用：Sales/Use Tax题改进后一次EM/F1=1/1，第二次回退；Highways & Streets CIP模型选行/值并通过复核，保存的引用字面量经新服务器投影后EM/F1=1/1（该数值投影0新模型调用）；Events Center Non-Taxable Bonds独立审查拒绝。不能把保存证据后处理写成新的模型端到端调用。
- 原页路径单独再跑通过；原PDF SHA、渲染SHA、问题SHA及三个PDF区域回放相符，八项review均true。产物：`docs/foundation/runs/real_document/real-agent-finance-20261011-*.json`。
- 本轮共29次DeepSeek API调用，113138 input / 3455 output tokens；峰价估算约$0.038，限30次/200000 UTF-8字节文本上下文/每次1000输出tokens；只发送公开OHR页与问题、检索内容。无训练和私有文档外传。A100宿主可见但当前解析容器无GPU设备节点；MinerU CPU，答案生成远程API。
- 改后专项`python -m pytest ict-track8/tests/test_m1c_model_budget.py ict-track8/tests/test_visual_source_fallback.py -q`：11 passed；代码`py_compile`通过。既有公式安全修复相关70项先前通过；本次未跑完整回归。未完成公式计算、三类语义对齐的真实跨源、保留集和总赛题验收。详见`docs/foundation/REAL_DOCUMENT_END_TO_END.md`。
