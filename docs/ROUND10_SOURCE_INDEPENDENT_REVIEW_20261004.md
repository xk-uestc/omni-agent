# ROUND10 原文多片段与集成独立审查

审查目录：`D:\ICT8-OmniAgent`。审查日期：2026-10-04。

## 范围与结论

本记录审查原文多片段通道、知识库生产集成、证据恢复和本轮评测工具；审查期间不调用模型 API、不读取凭据、不操作 Git。仅新增本记录，未编辑被冻结的后端、测试或工具。

当前范围内的来源重建、片段完整性映射、错误停止、成功答案保留和评测输入隔离符合实现契约。此结论不是整个赛题验收，不是形式化语义证明，也不意味着官方文档准确率已提高。

## 生产路径检查

| 检查 | 实际实现 | 结论 |
|---|---|---|
| 原件来源 | `KnowledgeStore._generation_citations` 从 SQLite 权威 chunk 和固定 SHA 的原件重建上下文；不把检索 metadata 的 `raw_page_text` 直接当成模型证据 | 符合 |
| 复合/枚举入口 | `_answer_hits` 在通用生成前调用 `bind_multi_source_answer`，仅 `gpt-6-luna / medium` | 符合 |
| 回答结构 | 1～12 个精确原文片段、最多 6 个子问、最多 1800 字回答；一个完整原句可以覆盖两个子问 | 符合 |
| 片段来源 | 复用 `_source_snapshot`、`_source_literal` 和有界 quote catalog；校验唯一上下文、原文 offset、完整数值 token、来源 payload hash、重复项及预算 | 符合 |
| 子问映射 | 片段必须覆盖全部明确子问；独立匹配清单必须覆盖全部子问，每个片段的每个子问归属均需实际匹配项 | 符合 |
| 枚举清单 | 独立审核匹配项必须位于对应子问的回答片段 offset 内，scope 不能替代漏答；审核布尔值严格全 true | 符合，但仍依赖模型语义判断 |
| 成功发布 | 成功后重建 fresh citations，比较证据及 omission ledger，再执行 `replay_multi_source_proof`；`finally` 再核验原件及生成 chunk | 符合 |
| 来源变动 | 原件/chunk 替换会被重新核验拒绝，返回之前的 `finally` 也执行；不能以旧引用发布新答案 | 符合 |
| provider 失败 | 多片段选择或审核出现 provider 未完成/模型未验证/异常时立即早退为 `insufficient_evidence`，不继续通用生成或其他模型请求 | 符合 |
| 语义拒绝 | 可以回到原有带来源的通用生成；同一 `_answer_hits` 不再调用一次相同多片段选择 | 符合 |
| 单片段旁路 | `bind_source_span_answer` 和其 replay 拒绝复合/枚举问题，`GroundedSpanAnswer.source_answer` 将其派发到多片段通道 | 符合 |
| 成功答案压缩 | 通用生成成功的复合/枚举回答保留完整 claims，不再进入单片段或 typed-slot 压缩 | 符合 |
| 证据恢复 | 已成功的 `source_multi_span_model_reviewed` 不再恢复；失败后只有全部当次 API 完成且存在合法生成 attempts 才可对新增来源进行一次有界恢复 | 符合，新增证据恢复不等于同输入重试 |

## 不可扩大解释的限制

1. `coverage_scope` 明确为 `supplied_evidence_only_not_entire_document`。检索页命中、8 个引用或 12000 字预算不证明已读完整 PDF 或穷尽全部匹配记录。
2. 独立匹配项清单能阻止“审核发现缺项但候选未包含”仍被发布，但审核模型本身仍可能遗漏匹配项。不能把全部布尔值为 true 当作形式化闭集证明。
3. 多片段审核语义拒绝后，通用生成依然依赖其原有 whole-question review；该审核也可能误判。当前实现不宣称完全消除了语义漏答。
4. replay 证明保存原文、来源 payload、子问契约、映射和输出保持一致，不会重新执行语义审核。
5. 本轮问句识别沿用明确子问/枚举的生产契约，不代表任意语言、任意隐式子问都能自动拆解。

## 评测工具检查

### 原件开发子集

`tools/evaluate_round10_multi_documents.py` 仅按生产 `question_contract` 选择原报告中的复合/枚举题，没有按答案或旧分数选择。`KnowledgeStore.answer` 只收到当前问题及 `top_k=4`，未收到官方答案、正确 document ID、证据页或未来问题。官方答案只在调用完成后进入评分函数。

该工具核对原报告协议和原 corpus 路径，并按 manifest 验证原 PDF SHA。固定计划分母为 6：失败、未评分和因授权错误未运行的题不从分母删除；F1 以计划题数作为分母。

其退出码仅表示源码稳定且计划执行完整，**不是效果通过门禁**。程序可在 6 题均无实质回答时返回 0；效果必须读 JSON 成绩、status、answer_mode 和实际 API audit。

每题创建模型/原件校验发生在题内结果捕获之前；此阶段若发生无法继续的初始化/原件错误，整个工具可能中断而没有最终报告。不能将不存在报告解释为缩小分母后的通过。

### 自建六题

`tools/evaluate_round10_authored_documents.py` 使用随机 campaign/test 标签，自建 6 题，生产入口只收到问题和实际资料。`required` 与预期拒答只在返回后评分，不输入模型。

固定 6 题分母包含 4 个要求实质回答的题和 2 个要求拒答的题。token 包含评分不证明一般语义正确性；报告已有该限制。

### 独立进程五轮

`tools/evaluate_round10_process_sessions.py` 每轮启动新的 Python OS 进程；IPC 只允许 `question`、`store`、`session_id` 三个字段。参考 SQL 只在父进程只读 SQLite 中执行，不输入子进程或模型。

每个领域固定 5 轮，两领域固定 10 轮。初始化错误、进程超时、授权失败、源码变动均留下失败或未运行记录，不缩小计划分母。每轮检查新 PID、完整上下文恢复、持久轮数、实际 API 模型、生产 SQL 的只读重放及期望结果。

## 已落盘证据

| 报告 | 实际结果与限制 |
|---|---|
| `ROUND10_SOURCE_REGRESSION_CANDIDATE_20261004.json` | 304 passed / 1 failed，必须保留；预设 `model_called=false` 不能证明该失败运行没有模型调用，见 erratum |
| `ROUND10_REGRESSION_CANDIDATE_ERRATUM_20261004.md` | 已说明 tmp_path 位于 runner 的 runtime TEMP 中，误测试了“允许范围”而非“拒绝范围”；未捏造未知 API 请求数量 |
| `ROUND10_SOURCE_REGRESSION_FINAL_20261004.json` | 305 passed / 0 failed，起止源码/测试稳定；当前源文件与记录后端 hash 一致。定向回归，非全量回归 |
| `ROUND10_AUTHORED_DOCUMENTS_CANDIDATE2_20261004.json` | 6/6，包括 4 个实答与 2 个预期拒答；全部已记录 API 完成。当前后端 hash 一致。不是官方或盲测准确率 |
| `ROUND10_PROCESS_SESSIONS_FINAL_20261004.json` | 10/10、2/2 完整五轮段、10 个独立子进程、10 次完整 context 恢复及持久轮数验证；两个数据库未变化。当前后端 hash 一致 |
| `ROUND10_MULTI_DOCUMENTS_CANDIDATE_20261004.json` | 6/6 执行，EM 0→0，F1 0.2311756667→0.1167348333，多题上游失败；属于此前候选版本，不能证明当前早退行为或效果提升 |
| `ROUND10_MULTI_DOCUMENTS_CANDIDATE2_20261004.json` | 6/6 执行，EM 0→0，F1 0.2311756667→0.2202953333，下降约 0.0108803334；47 次 API audit 中 1 次未完成。当前后端 hash 一致；2 个视觉回答、4 个证据不足，未有官方题多片段成功 |

对自建 CANDIDATE2 中三个 `source_multi_span_model_reviewed` 结果额外调用了当前 `replay_multi_source_proof`：`three_tests`、`enumeration_and_role`、`period_qualified` 均为 true。该额外检查只重放报告内保存来源及证明，不重新调用模型，不等于重新检查所有原始磁盘资料。

旧失败测试已改用明确的 `ROOT.parent / round10-outside-runtime` 路径，并用 `enable_local_model` spy 验证拒绝发生在模型配置加载前。当前 FINAL 的离线性质应结合修复后的测试调用路径与 mock 检查判断，不能只引用 JSON 中预设的 `model_called=false`。

原件 CANDIDATE2 已落盘。原 `b82d1659-2e69-432e-ad19-8aa397bf238e` 的单条元素记录不再被发布为完整枚举答案，本次为 `insufficient_evidence / extractive_fallback`。这证明本次没有重复旧错误的发布方式，但并未完成原题完整回答，也不能证明所有枚举题都已可靠处理。

当前已有证据支持“多片段表示能力及不完整答案拒绝有所补强”，没有证据支持“原件复杂文档整体准确率提高”。后续应优先增强有来源约束的完整记录检索/上下文覆盖，并继续保留本次全部拒答、provider 失败和评分结果，不改评分器或以旧答案作为模型提示。
