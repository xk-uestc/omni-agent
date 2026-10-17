# M1-B1 最小跨会话记忆闭环

本轮依据2026-10-10新附件授权实施 M1-B1。仅 memory 分支；QiMem 许可证未明确，不复制其代码。独立实现SQLite、可信服务端scope、确定性recall和候选事件存档，未实现自动提取/晋升、跨源经验或训练。

## 实际入口与数据流

| 源码位置（符号为稳定定位） | 实际行为 |
|---|---|
| `backend/app.py:memory_core/memory_adapter` | 服务启动从环境读取可信单项目scope；默认关闭，缺少完整配置不能启用；HTTP/SSE使用同一个core |
| `backend/omni_agent.py:OmniAgent.query` | 开启时进入Adapter一次，关闭时直接 `_query_without_memory` 原路径 |
| `backend/memory/adapter.py:MemoryAdapter.run/prepare` | 会话锁和一致读范围内取得Schema/来源；一次core.recall；候选仅存在于请求局部集合 |
| `backend/memory/core.py:MemoryCore.recall/invalid_reason` | scope、确认收据、时间、来源、Schema、指标/聚合/条件校验；合法同名候选冲突先于排序/预算，超出512候选不截断选取而降级 |
| `backend/memory/adapter.py:_snapshot/_matches/_filter_conflict` | 当前物理列/值与已有alias锚定；候选不包含SQL，记忆content从不作为指令注入；明确渠道冲突或移除条件则澄清 |
| `backend/memory/adapter.py:prepare` → `engine.extract_required_intent` | 将匹配术语转成当前已有alias与已验证的条件字面值，核实物理指标、函数、过滤条件及完整coverage；不改linker/catalog规则 |
| `backend/omni_agent.py:_query_without_memory` | canonical进入原语义归一化、路由、规则SQL或模型SQL链，保留原SQL contract/构建/只读执行器 |
| `backend/omni_agent.py:_remember` | 原问题保留；ContextVar传递的来源收据写入会话状态，finally清理；后续追问复验来源、同名冲突和显式条件，即使初始轮被历史窗口淘汰 |
| `backend/memory/adapter.py:run finally` → `core.observe` | 记录trace ID、问题hash、selected/consumed/rejected/source_versions、最终执行类别；模型失败与成功fallback分别留证；query.complete在observe后发布 |

SQL执行完成还核对物理计划的binding才标记consumed；来源重验不通过不发布最终答案。成功执行的 `execution_verified=true` 只代表有执行来源，并不代表独立任务评分通过；在线事件 `independent_task_verified=false`、`promotion=none`。独立评分只在tools中执行，Gold从不输入生产planner。

核心接口是 `recall(context,scope,budget)`、`observe(event,verified_outcome)` 与 `enabled`。Store保存业务候选与事件到不同表。事件以(scope,event_id)幂等，相同ID不同payload拒绝。Store异常通常回退无记忆路径；如果历史已依赖无法验证的记忆，则澄清，避免旧口径从历史继续执行。

## 配置与可信边界

默认 `ICT8_MEMORY_ENABLED=0`，不创建Memory Store。以下均由服务管理员配置，不来自HTTP请求或session_id：

```text
ICT8_MEMORY_ENABLED=1
ICT8_MEMORY_DEPLOYMENT=<trusted-deployment-id>
ICT8_MEMORY_PROJECT=<trusted-project-id>
ICT8_MEMORY_SOURCES=<database-source-id>,<authorized-document-id>,...
ICT8_MEMORY_DATABASE_SOURCE=<database-source-id>
ICT8_MEMORY_DB_PATH=<server-owned-memory-sqlite-path>
```

数据库来源必须属于sources；项目/部署/来源集合形成精确scope键。不得指向业务SQL数据库或知识原件。当前无客户端记忆写入/确认API；仅可信离线代码可调用 `MemoryStore.put(MemoryRecord(...))`。管理员必须先获取真实确认与独立核验收据，再填写provenance和 `MemoryAdapter.source_version(document_ids)`，观察事件不具备该授权。

第一版支持服务端单项目。任意知道session_id者仍可能访问已有会话机制；此项目没有本轮可证明的多用户身份系统，不能宣称多租户隔离。生产部署的来源授权和离线provisioning权限由管理员承担。

仅两个Omni入口接入，低层NL2SQL/workbench/澄清辅助端点未新增Memory能力。作用域名是服务端约定的数据库ID与KnowledgeStore document_id，不是路径匹配或用户自报来源。

## 版本与保守限制

Schema版本摘要包含当前表列、alias规则、metric catalog digest；文档来源SHA经过原件核验。数据库版本沿用原引擎 `file_generation_and_schema_version_not_content_hash`，不是全库内容hash。实验额外对数据库计算执行前后SHA256。任何版本变化默认失效，不能自动证明新版本仍保留原口径。

仅匹配有界字面术语；引号、现有实体值、定义请求、图片请求、危险指令不做术语改写。最长术语优先、ID打破平局；最多3项/1800字符；canonical只使用现有alias，必须通过原planner完整核验。复杂表达、无法表达的聚合与条件、预算不足等可能保守澄清；不是通用语义学习。

历史检查对可能继承上下文的请求保守复验窗口内收据，可能多澄清，需要后续独立history lineage设计；首版不扩大功能范围。跨worker会话序列化仍受原进程内锁限制。SQLite事件未设自动保留/删除策略，长期存储增长需后续治理。

## 冻结实验的合法候选来源

`tools/evaluate_memory_m1b1.py` 导入原独立scorer与fixture helper，运行前验证原manifest所有hash。Gold只在响应完成后传给score。task.candidate_ids只在测试provisioner定义隔离场景，生产代码不知道task ID。A/B使用相同候选、来源、数据库和输入；A关闭不调用记忆；B每请求固定recall。四题前置与目标session独立，目标历史0，并在边界重开SQLite Store；收益来自已确认fixture业务定义，非同会话历史。

开发候选的 `confirmed_fixture_contract` 由离线fixture明确转成confirmed，receipt标明不是已认证真人确认；失效/冲突/外scope/未确认是冻结对抗夹具。f01/f02的task_experience仅归档为不支持，未晋升或注入；M1-A已验证的DependencyAgent准备结果也没有作为答案提供给本轮planner。

185回归保持M1-A空知识库存；开启组Store装入同候选池的三个无关业务术语，绑定当前Schema版本（不伪装存在术语文档）。它验证非空Store不会干扰旧题，不能替代16题或新历史对抗测试。测试和报告均是公开开发夹具，不是官方成绩。
