# M1-A 源码审计（2026-10-10）

审计基准：`memory` / `a4ed8bb83fa09fcc4a3375709a02ff34d62f1d7f`。开始前 `git fetch origin memory` 成功，FETCH_HEAD 与 origin/memory 均为该 SHA。本地原在 main、仅有未跟踪 `Qimem/源代码.zip`；切换 memory 未覆盖该资产。main 不修改。本文所有位置为该基准源码的行号；建议尚未实现。

## 现有功能—记忆增量—实际接入点

| 已有功能 | 记忆新增能力（建议，未实现） | 准确调用位置 |
|---|---|---|
| 同会话 8 轮/1800 秒 TTL，可选 SQLite，逐会话进程内锁 | 跨会话、经验证业务知识；不是扩大历史窗口 | `ict-track8/backend/session.py:33,69,75,104,144`；`omni_agent.py:501` 在 turn 锁内读取历史 |
| 独立于短窗口的来源档案（24h/64 条），SQL/document 身份 | 记忆条目引用来源和版本，不把历史来源变成跨用户事实库 | `conversation_sources.py:7,19,39,49`；仅 document_context/executed_sql_context 被保存 |
| 语义待澄清解析、同快照归一化、历史 hint | 新会话复用合法术语/口径，冲突仍澄清 | `omni_agent.py:480,501–512,553–568` 在 normalize_question 前；`nl2sql/engine.py:302,315`；`nl2sql/semantic_graph.py:361,406` |
| 服务器验证 SQL 路由、规则或模型规划 | 同一候选影响所有 SQL 路径的语义解释，不依赖顶层 prompt | `omni_agent.py:386,1042–1085,1207–1209,1269`；`engine.py:341,685,1152,1197` |
| Schema/value grounding、指标目录、只读执行 | 记忆建议必须投影到当前字段/指标；不执行旧 SQL | `engine.py:895,1134,1218–1260,1322,1425`；`semantic_graph.py:239,406` |
| 有界检索选入规划的文档、元数据及 SHA | 经验证跨源方法作为短建议，当前证据仍重新获取 | `omni_agent.py:295,1095–1114` 中规划 context；`dependency_agent.py:114,245,315` 执行链 |
| 文档原件 SHA 校验与跨源版本重查 | 记忆 provenance 引用已验证的文档/定位；失效拒用 | `knowledge_store.py:277,290,1348,1497`；`dependency_agent.py:169,186,283–286` |
| 结果来源汇总、路由回执、query.complete | 统一 observe 候选事件，独立反馈后才能晋升 | `omni_agent.py:618–632`，attach_sql_sources 后、return 前，覆盖 _query_turn 提前返回 |
| HTTP/SSE 都调用 OmniAgent.query | 服务端 scope 同一解析器，传入相同 adapter/context | `app.py:229,383–395,398–453`；SSE worker 调用在 416，必须显式捕获 scope |

## 1. Recall 的真实时机

建议在 `OmniAgent.query` 的 `conversations.turn` 和 `ExitStack` 内，读取 reset 后有效历史/可信服务端 scope、解析现有 semantic pending 后，**首次 normalize_question（568）之前**召回。调用必须使用原始问题、有效当前历史和当前来源快照；在此之前先检查权限/作用域及预算。当前 `semantic_input` 只对固定 needs_normalization 短语成立，不能假定任意业务新术语会进入它。M1-B 如获批准需要 request-local 的显式候选归一化入口；不能只把新术语传给现有 normalize_question 就宣称完成。

pending 的已确认选择、显式本轮条件和当前来源优先；reset 清空临时历史，不自动清除项目记忆，但应禁用历史 hint。相同请求只召回一次，`_query_turn` 内部递归（679、773）复用该只读候选集，避免重复召回/重复 observe。多模态及写操作拒绝路径不得被记忆改写成可执行请求。

跨源经验可在 `omni_agent.py:1095` 的规划 context 加入独立 bounded `memory_hints`，附 memory_id、版本、证据和验证状态。catalogue 的文档内容仍是证据，记忆方法不是系统指令或资料原件。无模型时 `basic_plan:376–380` 对未完整跨源任务澄清；记忆建议不能冒充已授权任务图。

## 2. 绕过顶层 LLM 的路径

- `query:520–547`：无历史的写操作拒绝/缺少追问上下文；`574–593`：语义歧义，直接返回澄清。
- `_query_turn:672–800`：已验证证据回顾、排名结果追问、SQL 实体→文档桥接、来源档案指代；可直接返回或内部递归。
- `801–910`：关系范围编辑、待办目录/恢复、来源验证；`918–971`：比较批次、范围编辑、澄清续接。它们运行服务器核验执行器，不需要顶层 client.generate。
- `1057–1084`：VERIFIED_SQL_CONTEXT_MODES、direct_sql、FAST_SQL 显式公式、已验证 fusion 模板、排名文档重新绑定、术语定义路由都先于 `elif self.client:1085`。
- `basic_plan:360` 的 SQL 规则兜底在 1207/1209；直接 `engine.answer:1269`。顶层 LLM 不启用仍可执行 SQL。
- 顶层直达仍可能调用 **NL2SQL 自身模型**：`engine._model_plan:685`，`_answer_canonical:1265–1306`；不能把“跳过顶层规划”写成“零模型调用”。
- `/api/v1/nl2sql/query:895`、`/api/v1/agent/query:925`、其 stream:951 和 workbench clarify:491 不全经过相同顶层入口。第一版若仅覆盖 Omni 两入口，应明确这些低层 API 无记忆；未来若扩展再统一 request context，不能修改共享 engine 属性来偷渡候选。

## 3. 不改变 SQL 安全规则的语义接入

记忆只表达别名→当前 table/column/metric_id、统计函数、单位和确认条件，或者获取证据的方法。`SemanticGraph.normalize:406–455` 已将 selected_metric 校验到当前 bindings 并保护标识符/字面量；`_select:361` 保留歧义。可借鉴这些校验，但其 history_hint 是**既有同会话已验证提示**，不能把任意跨会话文本伪装成它。新的候选需独立字段和审计，且 `needs_normalization:135` 固定词表要被纳入设计。

运行时字段必须存在，metric catalog digest、aliases hash、Schema 摘要与证据来源版本必须匹配；指标条件不能静默覆盖本轮显式条件，冲突/未知映射必须澄清。不要修改全局 linker/catalog/engine（HTTP/SSE 并发共享）。`engine._normalize_in_snapshot:319` 缓存键目前含 question/revision/history_hint/catalog digest；新增候选需 request-local、包含有效候选版本/摘要或禁用该缓存，避免跨 scope 污染。

最终仍走 engine 的模型结构验证（720）、grounding（895）、服务器 required_intent 再提取（1218）、只读数据库 URI（213）、`execute_read_only:1426` 或 complete artifact 安全链（1411）。旧 SQL/旧数值不进入执行器，不以记忆权限代替数据库授权，不降低 row/step/time 限额。

## 4. 证据的可信程度

- 文档：`KnowledgeStore.verify_source:277` 检查当前逻辑记录 SHA，`_verified_asset:290` 重算原件 SHA 并拒绝符号链接/目录越界；citations 校验在 1348/1497。可信的是“所引用原件/版本/定位”，不是文档陈述本身必然正确。
- SQL：`engine:263` 固定一致性快照；`1458–1471` 返回执行 SQL、来源版本和 `source_revision_kind=file_generation_and_schema_version_not_content_hash`。`235` 明确版本是 stat/WAL/alias 生成标识，**不是数据库所有内容的密码学散列**。跨机器/重建库不可把该标识当永久版本。基线另外保存 DB 文件 SHA、Schema/alias/catalog hash 与独立只读 gold 重放。
- 跨源：`DependencyAgent.run:114–167` 重建 source constraints；`_document_versions:169` 只认可文档工具 provenance，不认可 SQL 单元格伪造 SHA；`_run_ordered:245–313` 重查来源，确认来源约束/终端公式消费。返回 source_validation 自己声明只是原件/逻辑版本，不是语义真值。
- 同会话 seal/source receipt：`sql_history_scope.py:24,393` 的 SHA 和 revision 检查用于服务端内部状态一致性；不是独立身份签名。`query:619` 的 attach_sql_sources 和 trace/audit_id 是追踪证据，不是独立任务成功评分。
- 可信晋升额外需要经认证确认事件或独立结果校验，绑定事件 ID、scope、当前数据版本和撤销状态；原始用户文本、assistant 回答、模型提出 SQL/任务图、tool complete、HTTP 200、SSE done、未完成/错误/待澄清执行，只能记候选。错误轨迹可保留经验证的失败原因，不能变成成功经验。

## 5. 服务端 scope 与两种入口

`app.request_security:229–246` 在生产校验单个 `ICT8_API_TOKEN`，没有用户/租户映射，也没有 request.state principal。`ConversationStore.validate_id:69` 仅格式检查，客户端可选 session_id。现状不能声称多用户跨会话隔离已成立。

建议首版只做服务器配置的单项目部署 scope（部署 ID/project ID + 已授权数据源集合），不使用 raw token/hash/session_id 为用户身份；没有可信 scope 则不召回、不晋升。认证确认不能仅用用户句子“已确认”认定。多租户方案需先由服务器认证中间件解析稳定 principal、tenant、project 与 ACL，再传 typed context。

`omni_query:384` 与 `omni_query_stream:399` 目前只有 Pydantic payload，无 FastAPI Request 身份上下文；未来由两入口调用同一服务端解析器，显式把不可变 context 传到 query，SSE 线程闭包携带该对象（416），不依赖会自动跨线程传播的 contextvars。观察挂在 query 汇总后（619–632），不能挂在客户端 SSE done 消费处：超时后 worker 可能仍继续，见 `app.py:438–445`。可靠事件需要 task ID 和幂等键；断流不代表任务失败，重复提交也不应重复晋升。

## 6. QiMem 实物与复用判断

实物：未跟踪 `Qimem/源代码.zip`，ZIP 内路径 `602993-QiMem-OS（麒忆）/源代码/qimem-os/`。只将 service Python 文件解到 `/tmp/omni-m1a-qimem` 供只读审查；未运行旧 runtime，未打开数据库/凭据，未复制进 Git。压缩包 SHA256：`eada4674b71fbfe087e5925680fbca2d55a2affc39dcb92d231c563ffd1a3650`。ZIP 文件名列表未发现 LICENSE/COPYING；不据此假定许可已获得，后续复制源码需先核实复用许可。

| 文件（QiMem 根路径下） | 可提取的逻辑/契约 | 耦合或不适用之处 |
|---|---|---|
| `service/agent/memory.py:12–32,93` | MemoryInput/Protocol、有界输入、source_id、reference 的 id/version/dependencies | `9` 导入旧 qimem_runtime（含 fcntl）；`39–75` 直接 Bridge，默认 confidence/authority=.7 不代表可信；require_context 仅非空校验，不认证 |
| `service/memory_system/governance.py:93–173,175–249,312–375` | 证据必须在原消息中、偏移定位、时区区间、撤回/更正、依赖失效、冲突 pending、审计 | trajectory digest/消息 seq-role-time 结构、个人偏好/reading.page 模型、旧 SQLite 表；不能原样当业务指标治理。用户引用存在不等于业务确认 |
| `service/memory_system/retrieval.py:16–51,67–140` | user/scope/允许 ID 在 top-k 前过滤、RRF60、稳定排序、去重、分阶段计时 | Bridge dense、旧 entries/sources 表、DeepSeek selector/Jina reranker、三层会话导航。默认 temporal_mode=evidence（保留所有状态）；current 模式仍含 ungoverned 文本，不能直接当可信记忆 |
| `service/memory_system/privacy.py:9–37` | 字段和值递归脱敏、JSON 字符串、PEM/assignment 规则，适合单独审查提取 | 纯标准库；Harness 注释不构成运行依赖。正则覆盖不等于完整隐私保障，业务数据应最小化保存 |
| `service/memory_system/runtime.py:46–90,133–180,242–260` | 版本不匹配拒绝覆盖、待处理任务/幂等 cursor、前缀 hash、失败保留 pending、发布后可见 | `fcntl.flock:54` 为 POSIX/Linux 特定，Windows 不直接可用；SQLite layers + SDK/vector 双存储；`75–85` C++ Bridge + 强制 kylin-real；模型抽取/重排/选择/提炼/关系模块；`ingest:136` 读取原生 Harness JSONL |

额外核对：`service/memory_system/bridge.py:41` subprocess 二进制 JSONL 协议；`trajectory.py:33–39` 限制 .jsonl 并调用 Node `harness/native-trajectory.mjs`。Omni 已有结构化结果，不应为了复用而制造 DSH/Harness 轨迹或依赖旧 Linux 服务。

指定文件 SHA256：

```text
memory.py      76cd3150b4d3002230b3d2774f6bec3c2ca2f4886678bc8d0894c1bc7160beac
governance.py  6321de12c2643659f6fe4dc53eb6247acccfc9ce48c51596bc8327a2e931f704
retrieval.py   40e6ad27c05def8bf94d054bd85b5932dcf11d25e92d89ca7a82419f63f04fce
privacy.py     3317cf7615e2f0fe68505e8cf448d0cacabacfe1701cc34b0b78ed5d46a122b5
runtime.py     f8aa3005f44fa9121c67e3872bb19c0ac954cdb12a02c9ff4265d92bd58dd6cc
```

## 7. 现有测试所支持的事实（读取，不等于本轮运行）

`test_session.py` 覆盖 TTL/持久化/深拷贝/进程内逐会话锁；`test_semantic_integration.py` 使用匿名字段/实际 SQLite 检查语义和路由；`test_dependency_agent.py` 使用 seed DB、原始 txt 与 Excel 做公式和动态来源；`test_api_security.py` 测共享 Bearer token；`test_omni_query_stream.py` 检查真实工具 trace 与最终快照/单次历史写入。185 回合工具 `tools/evaluate_context_semantics_20261009.py` 走真实 Omni→engine→SQLite、独立 gold 和输入记录集合，不用远程模型；历史 candidate-final 是 185/185。它们均不能单独证明跨会话记忆收益/多用户隔离。

## 推荐与备选（待 ChatGPT 审核）

1. 推荐：单项目部署可信 scope + query 前置一次 recall + request-local Schema/指标候选校验 + 规划 hints + query 末尾 observe；独立最小 Python Store，借鉴 QiMem 契约和治理思想。需要 M1-B 授权，当前不实现。
2. 备选：先建立认证 principal/项目 ACL，再接同一 adapter。适合确实需要多用户隔离，改动更大；不要将现有 session_id 升格为身份。
