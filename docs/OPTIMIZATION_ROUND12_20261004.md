# 第十二轮：显式非空字段计数与SQL血缘

日期：2026-10-04。仅对请求中明确、且物理来源唯一的字段非空计数添加COUNT字段投影约束，并保留复杂SQL原有结构校验与独立审核。

## 修改内容

- `complex_query.py`：为“staff.picture非空记录数”等唯一字段请求识别字段来源；SQL若只用`COUNT(*) WHERE field IS NOT NULL`代替，就触发既有的一次结构修复，并要求直接`COUNT(field)`。同一查询可以同时保留总行`COUNT(*)`和字段非空`COUNT(field)`。
- 局部语法限制为相邻字段计数表达。`return_date非空的租赁记录数`被当作带非空筛选条件的租赁数，不会误判为统计`return_date`字段数量。
- 同名字段或无法唯一绑定的字段不触发自动改写；原有歧义处理继续生效。
- `engine.py`：把新增的固定结构拒绝码纳入可观测安全原因枚举。

## 实际验收

| 检查 | 结果 |
|---|---:|
| 非空计数 + 日期/分组/安全专项 | **100 passed** |
| 字段归属和歧义专项 | **59 passed** |
| 全量本地回归 | **3398 passed，0 failed，2 skipped，2 warnings，12 subtests** |
| Sakila旧inline合同 | **8/56** |
| Sakila完整结果交付合同 | **48/56** |
| 五轮会话 | **17/20轮，2/4段全通过** |
| 真实API调用 | **133完成、2失败、135保留** |
| 本地8030真实模型HTTP验收 | **12/12** |

Round11完整结果交付合同为47/56；Round12为48/56。同一逐题配对有6题由失败变通过、5题从通过变失败，净增加1题。两轮均2/4完整会话，但Round12通过轮数18降为17，因此会话分数退步；单次小幅净升不能证明稳定泛化。Round12同样暴露开发问题集，不是盲测或官方成绩。

早期Round12候选把“return_date非空的租赁记录数”错识别为return_date字段非空计数，完整结果分数44/56。已保留`SAKILA_ROUND12_ARTIFACT_DELIVERY_FINAL_20261004.json`，修正后另存并冻结为`FINAL2`，不得混为同一实现结果。最终SQL中`cs-r01`按rental_date过滤、return_date只作非空条件并计算差值；`cs-f11`和`cs-s1-t2`都按payment_date和payment.staff_id成功；`cs-s4-t5`继续在五轮会话中生成审核通过的查询。

独立现场问题“统计全部staff的picture非空记录数”调用真实gpt-6-luna/medium，结果为0，生成SQL直接`COUNT(staff.picture)`，并通过独立审核。相同夹具以两非空值+一NULL实际得到2。详见`ROUND12_NONNULL_COUNT_PROBE_20261004.md`。

来源哈希审计PASS，报告为`ROUND12_RELEASE_SOURCE_AUDIT_20261004.json`。它只证明报告、SQL源、完整本地回归和HTTP验收与同一源码一致，不是准确率或完整赛题完成证明。

## 未完成

- 本轮OHR复杂文档准确率未重测；ROUND10已曝光六题为EM 0/6、F1 0.220295，现阶段最明确后续工作仍是来源内完整记录范围清单与关系/计算验证，方案见`ROUND11_DOCUMENT_RETRIEVAL_REVIEW_20261004.md`。
- 本轮Sakila有5题从Round11通过变失败，五轮上下文通过轮少1；应继续定位这些回归，不能只保留提升案例。
- 核心44题、陌生领域五轮、复杂文档总体三项目标均未因此全部达成。

真实完整观察在`D:\ICT8-OfficialDatasets\sakila-round5-cross-schema-20261002\runs\round12-final2-development-20261004`。逐题评分和全量回归见`SAKILA_ROUND12_ARTIFACT_DELIVERY_FINAL2_20261004.json`、`SAKILA_ROUND12_INLINE_FINAL2_20261004.json`、`LOCAL_REGRESSION_ROUND12_FINAL2_20261004.json`及HTTP/来源审计文件。
