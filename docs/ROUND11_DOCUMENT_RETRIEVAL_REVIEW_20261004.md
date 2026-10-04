# ROUND11：复杂文档完整记录范围检索复核

日期：2026-10-04。工作方式：只读代码和既有报告；未调用真实 API，未修改生产代码或测试。此文件是下一轮实施建议，不是已完成能力或效果提升证明。

## 结论

ROUND10 已补多片段表达及独立审核，但真实 OHR 六题没有提升：EM 0/6 → 0/6，平均 token F1 0.2311756667 → 0.2202953333。下一步应从“取更多相似片段”转向“先识别来源，再建立并核验该来源的完整记录范围”。不能放宽多片段审核，把部分检索结果称作完整答案。

现有失败不能全部归因于 12,000 字符上限：六题中的四次文本证据总量分别只有 3,133、4,181、3,774、1,739 字符，omitted 均为空。直接可见的问题是错误或无关来源占位、相关表行没全部进入记录集、关联和计算子问不能由原文拼接直接回答；另有一次真实 provider 失败。

## 检查证据和适用范围

- 报告：`docs/ROUND10_MULTI_DOCUMENTS_CANDIDATE2_20261004.json`。六题按生产 question_contract 的复合/枚举形状选择，不按正确答案或分数挑选；这是已曝光开发子集，不是完整官方成绩。
- 检索：`ict-track8/backend/knowledge_store.py` 的 `search`、`answer` 和 `_generation_citations`。
- 恢复：`ict-track8/backend/evidence_recovery.py` 的 `plan_evidence_recovery`、`recovery_eligible`。
- 选择：`ict-track8/backend/evidence_coverage.py` 的 `select_coverage_hits`。
- 多片段：`ict-track8/backend/source_multi_span_answer.py`。
- 页面目录：`ict-track8/backend/pdf_page_index.py`。现有 complete 仅代表所有页的 native strict-grid 索引扫描，**不代表无框表、OCR、图表或问题相关记录全部提取完整**。

报告中的官方答案仅属于评分字段；本复核不把这些答案、期望页码或题目 doc_name 作为下一轮模型或检索输入。下表的来源名称来自实际返回 citations，不是给生产程序提供定位标签。

| 题目 ID 前缀 | 实际输出及复核发现 | 不能据此推断的事项 |
|---|---|---|
| 02fea0e2 | 证据不足；恢复后 7 个候选、7 个选中，实际生成 6 条/3,133 字符；来源含实验报告与无关新闻。multi_selection_contract_invalid。 | 该错误码同时包含模型主动 abstain 和格式非法，不能断言一定是格式 bug；同日同分析员问题还需要完整记录的关系比较。 |
| b4e19614 | 证据不足、extractive_fallback；实验室两个相关元素行和样本位置可见，但同时含手册、新闻、财务材料；8 条/4,181 字符；multi_provider_failed。 | 不能把上游失败当作检索或数值计算故障；不能因两个数值在返回文本中出现就认为它们已核验属于同一样本。 |
| b82d1659 | 证据不足；8 条/3,774 字符中实验室表头重复、一个元素行可见，另有财务、新闻和 AIG 材料；multi_selection_contract_invalid。 | 已关闭单个元素冒充完整枚举的路径，不等于完整枚举已成功。 |
| c2d2cedb | visual_source_model_reviewed 实答，F1 0.838710 → 0.838710。 | 不应为检索改革重生成已成功的原页视觉回答来追分。 |
| b5973c4e | visual_source_model_reviewed 实答，F1 0 → 0.285714。 | 有原文及金额不自动证明整个复合问题已达到官方合同；需单独分解用途/交易金额及范围。 |
| b7cbe8c6 | 证据不足；恢复后 7 条/1,739 字符，唯一财务来源；multi_selection_contract_invalid。 | 此项不是跨来源污染；角色和出勤比例属于跨段/表格关联与计算，完整原文枚举接口不负责做除法。 |

## 当前路径的结构性限制

1. `search` 在全库进行 BM25；对当前英语请求，报告中的 dense encoder_languages=['zh']，实际策略是 lexical_unsupported_encoder_language。名为 bm25_dense_rrf 的阶段标签不证明本题真实跑过英文 dense。
2. 最初 top_k=4；检索的较大候选池最终按词面覆盖压缩到四条。恢复最多三条 query，每条再取四条，再按原题选择最多八条。相同表格的多行包含高度重复词语，不会产生很多“新词面覆盖”，易输给带有新词的无关文档。
3. 来源多样性惩罚虽然很小，但仍只是软导航目标；面对一个实验报告内的全记录枚举，应优先来源一致性及记录闭包，不能默认多来源更好。
4. 恢复审核是“当前给定证据中完整”，并且已正确声明 supplied_evidence_only_not_entire_document。扩大 top_k 仍无法证明源文档没有漏项。
5. 现有原生上下文以 block/受验证 continuation 为主；相邻表行不必处于同一个 native block。重新获取一个完整 block 不等于完整表或完整样本报告。
6. all-page strict-grid 索引只在已选来源内运行。若正确来源没进入候选，它不能挽救；无框表 strict-grid 无匹配也不能视为原页无数据。
7. `source_multi_span_answer` 明确不做 arithmetic_or_inference。因此“同日同分析员匹配”“浓度比较”“出勤百分比”等不应全部硬塞进原文片段通道。

## 推荐下一轮实现：两层导航、完整范围清单、类型化执行

### A. 文档层来源导航

新增只包含导航信息的 source-level registry：source_id/source_sha256、页数、源标题、章标题、正文实体标签及每页命中计数。原始提取文本仍固定于源版本；registry 不是答案证据。

对问题先生成不含答案的子任务描述，例如“报告里的测试记录 + 分析方法 + 分析日期 + 分析员”。候选来源排名以问题原始实体、实际正文标签和子任务覆盖为依据；不得输入官方 doc_name、gold、预期页码或正确数值。

先在较宽但受限的来源候选中保留独立来源族，再进行来源内召回；允许实验报告下多行被同一来源保留。若题目未唯一指定来源而多个报告拥有相同实体，保持来源分开，不默默合并样本。需要澄清时返回候选来源及实际标签。

建议原型预算：最多 6 个导航来源，进入深提取最多 2 个；来源扫描总页/字节/秒预算沿用现有安全限制。预算数值应由冻结测量确定，不能为了这六题无限放大。

### B. 来源内完整记录范围清单

定位命中页后建立 scope manifest，而非直接把新 top-k 片段拼给模型。建议字段：

```text
source_id, source_sha256, extractor_version
scope_kind: explicit_page / native_table / report_section / sample_record_set
scope_identity: source-derived sample/report/section identifiers
candidate_pages, inspected_pages, omitted_pages
members: block_id / table_id / row_id / column_path / bbox / raw_text_sha256
header_members, unit_members, footnote_members
continuation_checks, rejected_regions, budget_exhausted
coverage_status: complete_for_declared_scope / incomplete / ambiguous
```

完整性只能由执行器检查出来：声明范围内每页已检查、每个候选表行已接受或明确拒绝、续表关系一致、表头/单位/脚注保留、没有被截断或预算终止。`complete_for_declared_scope` 永远不等于全库没有其他匹配报告。

无框表需要原页字词坐标行列重建，复用现有 native_aligned_table 能力，但拒绝模糊列边界和标题误作数据行。范围合并需可复核的报告号/样本号和重复表头；页相邻、相似文字或同一分析员均不足以证明样本一致。扫描件需显式 OCR/视觉字词确认，不能把 native_text_present=false 当作空页。

分批扫描可把范围做完整，而单次生成仍保留 8 条/12,000 字符上限。扫描结果进入带原文坐标及 hash 的记录结构，分批输出给模型仅用于导航或审核；模型摘要不成为新的事实。若全部必要原文超过回答预算，返回范围限定或可下载明细，而不是静默删掉尾行后称作完整枚举。

### C. 区分原文枚举与关系/计算

- 原文枚举：根据 scope manifest 的完整记录集筛选匹配行。回答成员必须等于已核验匹配集合；明确本次来源/样本范围。不能依赖模型 inventory 猜全量。
- 同日同分析员：生成类型化“记录键、方法、原始日期、分析员”关系。每字段绑定同一原页行，日期规范化需要明确格式与时区契约；先做严格字面日期一致性，未知日月顺序不得猜。执行器按明确键比较，返回记录来源。
- 浓度比较：两数值、单位、检测限定符和样本键全部核验后才能比较；`<200`、检出限、不同单位和不同样本不是普通浮点数。禁止用相邻位置替代样本一致性。
- 出勤百分比：人数/场次分子分母是独立原页事实，先验证表头、姓名、统计期间、单位，再做本地有界 Decimal 运算。角色段落仅通过已核验实体键关联，不由模型凭名字相似补关联。
- 模型审核继续审核整题和原证据；执行器证明来源及计算，模型审核不升级为形式语义证明。

### D. 错误和恢复可观测性

把 `multi_selection_contract_invalid` 拆成 `selection_abstained`、`selection_shape_invalid`、`no_eligible_original_context` 等真实原因，保持拒绝行为不变。保留 source coverage incomplete、ambiguous sample binding、unsupported comparator、provider failure 的独立阶段。

Provider 失败不启动更多恢复调用；完整记录集缺失才启动受限导航。已成功视觉/原文回答不为高分重新生成。记录第一失败及后续运行各自的源码、源文件和范围清单 hash。

## 验证和采纳门槛

| 层级 | 必须检查 |
|---|---|
| 单元/属性测试 | 同一表 30 行重复词，漏尾行必须 incomplete；跨页缺表头/不同样本禁止合并；相同姓名不同来源分开；预算中断不声明 complete；源文件变化使 manifest 失效；无框表/扫描缺行不能当空记录。 |
| 来源导航测试 | 大量无关正文与相同通用词的干扰源；只提供 question + store，不提示正确 doc/page；记录真正进入深提取的来源及页范围。 |
| 关系与计算 | 行级字段来源、检测限定符、单位转换、日月歧义、NULL 和零分母、实体同名歧义。 |
| 实际 OHR | 冻结同六题前后比较；保留失败，无 gold/doc_name 输给模型。再测未参与开发的题目或整套固定集，防止六题专项过拟合。 |
| 采纳 | 真实总效果提高且既有成功题、provider失败守卫、原页回放和调用预算无退化；展示调用次数与时延，不能只看 EM 或只看人工样例。 |

## 风险与实施顺序

优先做 **来源内范围清单 + 记录枚举**，再做 **关系/计算通道**，最后评估是否需要英文 dense。英文 dense 可以改进候选导航，但不能证明完整记录覆盖；不应先换 embedding 后宣布复杂文档已完成。

主要风险是错绑来源、跨样本合并、无框表列错位、扫描漏行和高昂全页扫描。解决方向是版本固定、范围有界、行列原文坐标重放及明确 incomplete，不是降低审批阈值。现有页面目录和上下文代码可复用，但两者都必须保留各自真实覆盖声明。

本轮仅有上述只读诊断与方案，没有新增真实验收或准确率提升证明。
