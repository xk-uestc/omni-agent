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
