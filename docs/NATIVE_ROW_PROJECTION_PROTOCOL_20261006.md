# 原件字段投影协议 Round30（验收中）

## 实现

选择计划必须显式指定 `projection_mode`。`whole_fields` 只允许 `fragments=[]`，服务器在原件有界表链中按已验证 AND 字面条件枚举全部匹配行，输出每个指定原字段全文。`exact_spans` 保留逐行逐列唯一字面绑定；不完整片段不自动变成完整字段模式。

源 SHA、原行顺序、单位/星号/不等号、竞争来源、独立审核全部保留。字段模式不能覆盖任何审核拒绝；固定数量冲突仍保留全部记录并澄清，公布计划规范化为完整字段模式。收据升级 v5，旧 v4 不被当作新协议重放。计划 trace 增加模式及片段数量，未记录凭据。

## 当前证据

- 上一轮完整回归 `LOCAL_REGRESSION_ROUND29_MARKERS_20261006.json` 已结束：4689 passed、0 failed、3 skipped、12 subtests passed、4 warnings，起止源码一致。它不覆盖本轮 v5。
- 本轮相关测试 78 passed，覆盖两种模式、全清单、名称标记、拒绝空/残缺精确片段、拒绝完整字段模式非空片段、错误列、旧收据、独立审核否决、模式篡改及计数冲突重放。
- 新随机匿名开发题 `NATIVE_ROW_SELECTION_GENERALIZATION_ROUND30_PROTOCOL_20261006.json`：7/8，5 实质回答、2 有依据澄清，源码稳定。与上一轮随机原件不同，不是严格成对改善。anonymous-5 这次计划为 whole_fields 且空片段，随后独立审核拒绝；回退整行引用不满足原件精准投影验收，仍失败。
- `NATIVE_ROW_PROTOCOL_REVIEW_DIAGNOSIS_ROUND30_20261006.json`：保留同一失败原件 SHA，仅原问题进入生产规划与审核，独立重试成功且源重放通过。该诊断不覆盖原失败，不把 7/8 改成 8/8；初次审核完整响应未保存，不能从重试结果推断初次哪个检查项失败。
- 公开复杂六题 `OHR_MULTI_ROUND30_PROTOCOL_20261006.json` 已完成，起止源码稳定：EM 0/6、F1 0.373399。铁锰题及出席比例题本次完成独立部件/整问审核，ICP 固定数量题仍要求补充范围；不能把状态 ok、F1 或个别审核成功当作整套准确率达标。Round29 F1 为 0.354604，Round28 为 0.415818，本轮仍低于 Round28，保留全部结果，不择优报成绩。
- `NATIVE_ROW_PROJECTION_MARKERS_ROUND30_PROTOCOL_20261006.json`：10 个实际原件投影字段、0 个可见边界标记遗漏、来源重放全部通过，无新增模型调用；这是字段显示诊断，不计问答得分。
- 整套原有 36 题通过新增 `--all-cases` 入口复测已结束：每一原题均执行生产入口，保留原 PDF、问题和评分函数，无题型/成功筛选，2 个独立客户端并行。输出 `OHR_ALL36_ROUND30_PROTOCOL_20261006.json`，36/36执行评分，起止源码一致；旧 Round9 EM7/36、F1 .403259，本次 EM8/36、F1 .459383。两个版本间有多轮其他修复及模型波动，不能将改善完全归因于 v5，也不能称未见泛化、完整 OHR-Bench 或达到70%。
- 本轮完整回归 `LOCAL_REGRESSION_ROUND30_PROTOCOL_20261006.json` 已结束：4704 passed、0 failed、3 skipped、12 subtests passed、4 warnings，源码稳定，890.469 秒。它与上述模型复测覆盖同一后端候选源码；部署/版本同步须另外核实。
- 匿名评测工具补充首轮选择规划和独立审核的原始结构响应，只观察，不替换、重试或给模型发送参考答案。该改动发生在本轮匿名评测结束后，本轮 7/8 原报告不补写观察数据；工具语法检查通过。

## 审核失败的后续核实

`NATIVE_ROW_PROTOCOL_FIRST_REVIEW_CAPTURE_ROUND30_20261006.json` 是另一组新随机8题，首轮规划/审核真实值保留，7/8（6实质回答、1有依据澄清）。此次失败是 anonymous-4 数量冲突题：模型认可筛选、全部4行、字段限定符及不挑选子集，却令 answer_or_clarification_is_supported=false、approved=false，生产仍拒绝。没有拿上一组或诊断的成功来补这组失败。

`NATIVE_ROW_REVIEW_RATIONALE_PROTOTYPE_ROUND31_20261006.json` 仅在同一原件诊断中扩充审核响应的 failure_findings，再原样交回所有原布尔检查；不修改生产审核，不覆盖否决。此次重试返回全部支持、空问题列表，澄清与原件重放通过。单次诊断不能证明该扩充机制稳定有效，也不替代首轮7/8。

## 评分影响

目前工程预估仍为 59—73/100。自建八题、协议单测、原件重放及诊断都不能替代官方 RAG 整问正确率；没有新的证据支持至少 80 分。后续先核实公开原题和完整回归结果，再决定是否采纳、部署及同步 GitHub。

## 整套失败后的修复优先级

36题本次暴露的真实问题包括：邀请活动、机构职责等六个来源片段选择弃答（trace中的 selection_abstained）；USAID金额题出现 source_literal_missing_or_ambiguous 并回退长引用；AIG复合问题的 multi_selection_contract_invalid；跨年度利润判断的 source_literal_cannot_execute_boolean。ICP固定两项问题仍不能从全匹配清单任挑两个，不能为了原参考答案删掉真实Sr。

OSHA两题本次输出3.13 mg/m³、1.0 ppm，参考仅3.13、1.0，EM为0；单位是有效来源内容，不能删除单位以提高EM。问题诊断必须区分真实未完成、表格/计算缺失、模型协议弃答及表述差异，不把这些混为检索召回失败，也不因此给语义正确率补分。
