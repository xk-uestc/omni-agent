# 原件清单筛选与数量冲突独立审核（Round27）

## 原始失败证据

Round26匿名八题为7/8，anonymous-8未通过审核。`NATIVE_ROW_SELECTION_DIAGNOSIS_ROUND27_20261006.json`保留同一原问题的独立重新调用，不覆盖原失败、不算新的准确率成绩。诊断显示：规划选择正确方法和人员条件，审核返回完整四行清单，`all_matching_rows_in_supplied_sources_covered=true`、`answer_or_clarification_is_supported=true`，但`filters_and_projection_match_original_question=false`、`approved=false`。只能由这些可观察输出推断审核可能混淆了问题指定的“两条”和字面筛选，不能断言模型内部原因。

## 修正与不变量

- 将题目要求数量、完整匹配数量、是否冲突及所需输出状态分别传入审核，不把题目要求数量编译为限制行数。
- 维持全部既有独立审核条件，新增`requested_cardinality_handled_without_subset`，所有布尔值必须为真，完整行清单仍须严格匹配来源顺序。
- 明确区分实体/方法/人员筛选与输出数量；数量不符只支持有依据的澄清，不支持成功回答或任意子集。
- 服务器不依据数量冲突覆盖任何模型否定；错误筛选、缺项和数量审核拒绝全部继续停止，未核验来源不发布。
- 收据版本更新v3；旧版或缺少新增审核字段的收据不接受新版重放。原件SHA与完整字段重放保持不变。
- 不重试刷结果，不改变参考答案、评分器或已有失败报告；仅用gpt-6-luna/medium。

## 实测

相关原件选择和比较测试54项全部通过。新测试覆盖：完整四行与竞争行仍进入审核；数量审核拒绝不发布；筛选/清单拒绝不被数量冲突覆盖；缺少审核字段与旧收据不接受；没有指定数量和数量刚好匹配不触发冲突。

`NATIVE_ROW_SELECTION_GENERALIZATION_ROUND27_20261006.json`：重新生成标识、人员、方法、表头别名、行顺序和平移的8个PDF场景，**8/8**，其中6道实质回答、2道有完整清单与独立审核的数量冲突澄清。源码起止一致，参考字段只用于独立评分。较Round26的7/8有进展，但随机输入和模型波动同时存在，不能把全部改善归因于单一补丁，也不是官方/未见基准准确率。

`OHR_MULTI_ROUND27_CARDINALITY_20261006.json`已完成：6/6执行评分、源码稳定、EM0/6、F1 .364404（Round26为.355594）。ICP固定数量题明确说明完整原件匹配5条而问题要求2条，独立审核通过；它仍是澄清，不算问答正确，参考答案和旧失败均保留。不能把status ok或F1当整问正确率。

当前版本整套56题NL2SQL复测已结束并分开评分：`SAKILA_SCORE_ROUND27_ARTIFACT_20261006.json`认证分页完整结果51/56、会话19/20、3/4组五轮全通过；`SAKILA_SCORE_ROUND27_INLINE_20261006.json`单次响应口径7/56，评分器未修改，全部5道完整交付失败保留。两口径不得混用或替换历史数据。本轮没有修改SQL生产模块，较Round23的48/56不能归因于原件筛选补丁；新结果为固定当前版本开发重测，非官方或盲测，模型波动尚未消除。按当前结果条件代入SQL90%门槛，工程总预估59—73，不代表已获10分或80分。

新的单题SQL诊断遇到报告序列化错误：`SAKILA_JOIN_DIAGNOSIS_ROUND27_20261006.incomplete.txt`保留部分原始输出；离线恢复`SAKILA_JOIN_DIAGNOSIS_RECOVERED_ROUND27_20261006.json`保留3个规划/修复/审核返回，0次额外API请求，缺少最终结果和完整调用审核，不将它当完整实测或覆盖原失败。诊断工具已修复QueryResult转字典，并在创建文件前完成序列化。候选SQL的无FK直接共享子键连接被拒绝后，修复SQL通过结构检查；独立审核又对原生COUNT/明确name分组提出疑虑，尚不能据此取消审核。

最终全量回归已结束：`LOCAL_REGRESSION_ROUND27_CARDINALITY_20261006.json`，4680 passed、0 failed、3 skipped、12 subtests passed、4既有警告，ok=true、implementation_stable=true。仅证明本版本地工程回归，不证明RAG达到70%或80分目标实现。

8030已重启加载Round27，PID38756，`/health`、`/`、`/knowledge.html`全部HTTP200。之前服务日志保留，新日志在`runtime/server-native-cardinality-round27.*.log`；不为健康检查额外调用模型。

## 后续效率瓶颈的只读观察

在公开水样原件的一份完整registry上保留两页全部页文、所有15行、每个字段的列号/表头/文本/数值标记，只剥离重复几何和成员结构，JSON展示字符从69,778变为13,763（约减少80.3%）。这是未应用到生产的候选输入压缩观察，不是token测量或时延/吞吐成绩。生产收据、原件校验及来源定位仍需保留完整几何；不能为了节约输入裁掉竞争行或原件页文。
