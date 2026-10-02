# 第六轮优化说明（2026-10-02，固定源码验收）

**当前结论：综合开发题41/44→43/44，Sakila跨库14/56→15/56，RAG F1 .317567→.327826、EM仍7/36；显式完整结果验证4/6→6/6。** 本轮有有限提升，四项顶级效果与泛化目标仍为`active`。三组真实模型、本地完整回归和完整结果报告的全部82个backend文件均对应相同最终源码；封存见 [ROUND6_FINAL_SOURCE_AUDIT_20261002.json](ROUND6_FINAL_SOURCE_AUDIT_20261002.json)。

## 1. 最终实现范围

本轮以主树 `81392e0` 的已审查实现为准；后续真实验收报告记录开始/结束源码摘要，用于确认运行期间实现稳定。主要变更如下。

| 范围 | 最终行为 | 边界 |
|---|---|---|
| 通用 SchemaLinker | 使用英文实际表名和已有中文表别名做局部 owner 限定；按字段别名实际出现的词段绑定数字维度角色；消除“统计数量”跨词形成“计数”的误判；短别名不吞掉另一处裸字段歧义。 | 裸字段、共享别名、未知或否定限定、同表多角色等真实歧义仍须澄清，保留 engine/provider 强门与 nullable COUNT 约束；没有新增 AdventureWorks/Sakila 专用别名规则。 |
| 显式完整 SQL 结果 | 独立 NL2SQL API 的 `complete_results=true` 产出可分页读取的完整 artifact、绑定来源/执行器摘要的 receipt，以及持久 HMAC 签名。预览上限与查询语义分别记录。 | 默认 Agent 和冻结 56 轮评分器仍走原路径；完整结果开发验证不能计入或修补冻结评分器成绩。 |
| 完整结果 scope | 排名并列与行数帽冲突、未知数字数量以及不支持的行数表达在执行前拒绝；9类行数请求动词采用统一解析规则。 | 返回HTTP422，要求调用者明确范围后再提交；不能把预览100行当作语义LIMIT，也不能截断应保留的并列结果。 |
| 来源与时间预算 | 验证原件/producer 摘要、SQL 执行、序列化、签名、发布和分页的预算及来源一致性。 | 执行预算为协作式检查，不能宣称可硬实时抢占阻塞 OS I/O；模型规划不计入 artifact 执行预算。 |
| RAG 恢复导航 | 从有界原件重建导航，最多 8 项、合计 12,000 字符；预览标记 `excerpt_navigation_only`，模型调用前后核验来源和切片。 | 不将命中项的 `raw_page_text` / `raw_text` 或便利 metadata 当作权威正文。 |
| RAG 审计边界 | 线程局部 `audit_generation` 与 `(generation, count)` 快照防止中途 reset 隐藏此前失败。 | 此门控制补充检索，不能扩写为旧 answer pipeline 任一操作失败后停止所有其他操作。 |
| 报告封存 | 解析和 SHA 来自同一份已读取的报告字节，核验保存计数、唯一 ID、失败计数、HTTP/响应模型，以及基线、observed 和冻结评分器 provenance。 | 封存器不重新评分、不改分；RAG compare 的 optimized SHA 必须与实际 RAG 报告 SHA 一致。 |

相关主树提交包括 `6b67f72`（通用 SchemaLinker）、`e6e477b`（原件导航和审计 epoch）、`b3fefb1` / `81392e0`（完整结果签名及 scope）、`30346fa` / `57fffbd`（报告核验和同字节封存）。完整结果分页容量清理与多用户隔离仍需后续扩展。

## 2. 独立审查

独立审查只使用合成探针和合同回归，真实模型 API 调用为 0。详细记录见 [ROUND6_INDEPENDENT_REVIEW_20261002.md](ROUND6_INDEPENDENT_REVIEW_20261002.md)。

| 审查项 | 已完成证据 |
|---|---|
| Schema owner、角色和 COUNT | 隔离工作区 21 个相关测试文件、471 passed；新增 22 项合成合同。 |
| RAG 原件恢复与审计 reset | 56 passed；旧 epoch 拒绝、新请求新快照可用、起点 0/3 和线程隔离探针通过。 |
| 完整结果 scope | 修订后 9 项独立 scope 探针通过。 |
| 持久 HMAC receipt | 5 项边界通过：重启/缓存淘汰可读，缺 key 不创建，换 key 或客户端重算公开 SHA/binding 不能绕过验签。key 不进入 Git、程序包或 API 响应。 |
| 总时间预算 | 12 项独立 deadline 探针通过，包含原 0.06 秒反例在约 0.062 秒时拒绝。 |
| 报告封存 | 14 项合成探针通过，覆盖重复 ID、计数不一致、错误 HTTP/模型及 provenance。 |

这些证据说明合同与边界行为通过检查，不能转写为未见题目上的泛化准确率。独立完整结果相关增量合同为 289 passed、2 skipped、1 warning；主树最终全量回归采用下一节报告。

## 3. 最终本地回归

正式采用 [LOCAL_REGRESSION_ROUND6_FINAL_REVIEWED_20261002.json](LOCAL_REGRESSION_ROUND6_FINAL_REVIEWED_20261002.json)。

| 项目 | 最终结果 |
|---|---:|
| passed | 2858 |
| failed | 0 |
| skipped | 2 |
| warnings | 1 |
| subtests passed | 12 |
| exit code | 0 |
| 模型调用 | 0 |
| 运行期间实现稳定 | true |

两项 skip 为 Windows 创建真实 symlink 的权限限制；跳过不能计入通过。先前 2840 项回归之后仍有 scope 修订，其报告属于历史证据，不作为当前最终回归结论；`FINAL` 等中间文件也不替代 `FINAL_REVIEWED`。

## 4. AdventureWorks 完整结果开发验证

正式采用 [COMPLETE_RESULTS_ADVENTUREWORKS_ROUND6_FINAL_REVIEWED_20261002.json](COMPLETE_RESULTS_ADVENTUREWORKS_ROUND6_FINAL_REVIEWED_20261002.json)。此次对已有曝光的 AdventureWorks SQLite 开发案例使用显式 `complete_results`，实际完整 artifact 检查为 **6/6**，模型 API 调用为 0、专用领域别名规则为 0，运行期间源码稳定。

| 验证对象 | 原路径/预览 | 完整结果与分页核对 |
|---|---|---|
| 两项大结果 | 原路径各 100 行，预览仍各 100 行。 | 完整结果各 19,119 行、各读取 192 页，全部 cell 与 reference 相等。 |
| 其余四项 | 10、10、1、3 行。 | 完整 artifact 分别为 10、10、1、3 行，逐项检查通过。 |

**19,119 的单位是行/分组，不是 cell 数。** 这里验证的是结果完整性和分页一致性；问题已曝光，且没有调用模型规划，所以不能称为真实模型 6/6、盲测 6/6 或官方评分器 6/6。此验证没有阅读新冻结 56 轮问句/oracle，没有修改旧 evaluator/scorer。

## 5. 真实模型验收：三组均已结束

本轮所有真实模型 API 统一使用 **`gpt-6-luna` / `medium`**。只按已结束报告填结果，不从进度快照推算最终成绩。

| 验收组 | 规模 | 当前状态 | 已结束结果 | 证据 |
|---|---:|---|---|---|
| 综合功能开发验收 | 44 项 | 已结束，`some_cases_failed` | 43/44；上一轮 41/44；实现 stable。 | [REAL_MODEL_CORE_ROUND6_ACCEPTANCE_20261002.json](REAL_MODEL_CORE_ROUND6_ACCEPTANCE_20261002.json) |
| OHR原件问答固定子集 | 36项 / 15原件 | 已结束，失败全部保留 | EM7/36持平；F1 .317567→.327826；实现stable。 | [OHR_ROUND6_FIXED_SUBSET_ACCEPTANCE_20261002.json](OHR_ROUND6_FIXED_SUBSET_ACCEPTANCE_20261002.json) |
| Sakila自编冻结跨库验收 | 56轮 | 已结束，56/56执行，stable | 14/56→15/56；独立题2/36→3/36，会话12/20持平，整段0/4持平。 | [SAKILA_ROUND6_OPTIMIZED_ACCEPTANCE_20261002.json](SAKILA_ROUND6_OPTIMIZED_ACCEPTANCE_20261002.json) |

综合组的 43/44 是已有合成开发案例上的真实 API 效果，不是公开基准或盲测准确率。未通过的 1 项 SQL 案例为 `model_router=false`，其余检查（含结果值）通过；这项仍按冻结判定计为失败。

综合组共 112 次 API 调用，112 次 retained、109 completed、3 failed、0 dropped，不能表述为全部 API 成功。可见 `total_tokens` 为 471,697，但 3 次调用 usage 未知，因此只可作为已报告总量的下界；输入/输出等 token 汇总也不完整。第三方价格未提供，成本不估算。

RAG的4题F1改善、2题退步、30题持平，配对见 [OHR_FIXED_SUBSET_PAIRED_ROUND6_20261002.json](OHR_FIXED_SUBSET_PAIRED_ROUND6_20261002.json)。160次API全部completed、0丢失，完整usage677458 tokens。该组是已首测的固定资源偏置官方子集回放，不重新称为新文档盲测；F1是词法重叠，不是准确率或事实蕴含证明。较早FIRST的8/36、.353459属于另一实现，不能选取代替当前成绩。

Sakila的2题由失败变通过、1题退步，净增1题；40次clarification与1次required_source_reads失败保留。76次API中71 completed、5 failed、0丢失；可见usage413960是下界，5次未知。runner没有打开reference，评分器在独立进程中核对SQL重放、实际全行/列、来源、历史及模型审计。这里使用上游公开虚构样例与自编56轮，不能称赛事官方问数题或公开榜单；完整artifact opt-in没有进入该冻结评分器。

综合组3题改善、1题退步；Sakila2题改善、1题退步。变化与失败明细、报告SHA、相同输入及评分器指纹保存在最终封存报告。三组共348次API，340完成、8失败、0丢失，可见total1563115 tokens为下界。没有为提升分数修改评分器或删掉失败，也不从单次运行差异断言统计显著或确定因果。

## 6. 结论与限制

- **目标保持`active`。** 四个核心模块已有实现；没有一项拥有足够证据宣布顶级效果和通用泛化达标。综合开发高分不能抵消跨库15/56、RAG EM7/36和完整新领域会话0/4的缺口。
- 模型存在运行波动。上述单次成绩和上一轮差异不足以证明稳定提升，必须保留失败调用、原始评分口径和可追溯源码摘要。
- 数据曝光限制继续有效：不阅读新冻结 Sakila 题目、reference、observed，也不阅读新 OHR 问题/答案/oracle；独立审查及本文使用匿名汇总、结构键和已曝光开发证据。
- 真实模型效果、完整结果合同、原件恢复安全门和报告封存分别有明确证据边界；不能将局部合同通过扩写为整条回答流水线已正确、数据已无风险或项目已经全面达标。

## 7. 下一轮重点

1. 通用Schema语义层：基于实际表/字段/外键与已有元数据建立中英字段概念、局部来源和问题要素的可审查绑定，分别区分真实歧义与画像覆盖不足；保持未知限定及nullable COUNT边界。
2. RAG完整语义：补足原生表头/单位/公式参数及跨栏条件的绑定，对答案全部子问逐项核查原件；原件上下文扩大与有界导航只能作为输入改进，不能自动提升精确匹配。
3. 新领域五轮：验证每轮来源、主题替换和事实行保留；已有Sakila结果仍是0/4整段，不能用其中12个通过turn宣布连续对话达标。
4. 每轮继续固定输入、源码、分母与独立评分；大结果的默认Agent接线、容量管理和多用户隔离另行验证。
