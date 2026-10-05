# 单表记录计数口径（Round23）

## 已完成的上一版全量验证

`LOCAL_REGRESSION_ROUND22_GROUP_FINAL_20261006.json`已结束：4617 passed、0 failed、3 skipped、12 subtests passed、4既有警告，源码起止稳定。它覆盖Round22分组来源补丁，不覆盖下述随后新增的记录计数补丁。

## 本轮修改

`complex_query._verify_scalar_record_count`从原始问题识别字面“记录数／记录总数／行数”等，只在单一物理表、单个直接标量COUNT输出、无JOIN／GROUP／HAVING／嵌套时校验：COUNT(*)及Schema声明非空字段可以计行，nullable字段不能替代记录口径。显式COUNT表达式、非空/空值、去重、分别计数均不创造此契约，仍由原有专项审核处理。不能推断跨表实体粒度，不能将COUNT字段血缘伪造为主键。

不合格候选进入已有且唯一的结构修复，不增加重试；修复提示保留所有原过滤和日期范围，不得添加IS NOT NULL或更换时间字段。独立整题模型核验、原SQL执行和血缘提取仍执行。

含NULL的SQLite独立示例：整表COUNT(*)=3，COUNT(event_date)=2；日期过滤后两者均2。即使某范围数值偶合，也不能据此更换记录与字段计数口径。新增测试覆盖NULL、时间过滤、显式字段非空计数、去重、分组、明确COUNT表达式、唯一修复以及原SQL执行。第一轮失败发现Python中文与拉丁字母之间的`\b`边界不适用，已改为ASCII标识符边界；未改旧测试。

终版290项复杂查询、关系查询、计数、Schema等相关测试全部通过、1既有警告。完整本地回归`LOCAL_REGRESSION_ROUND23_RECORD_COUNT_20261006.json`与三工作线程完整56题Round23已启动，结束前不声明全量通过或整体提分。五题隔离真实模型验收`SCALAR_RECORD_COUNT_MODEL_ROUND23_20261006.json`已结束：5/5模型规划、正确值及原SQL独立只读重放通过，源码稳定；这是暴露开发题，不是公开成绩，不是完整模型传输协议评分。

## PDF诊断

`DOCUMENT_ICP_DIAGNOSIS_ROUND23_20261006.json`整问失败；`DOCUMENT_ICP_DIAGNOSIS_DETAIL_ROUND23_20261006.json`同题再次返回原文Aluminum、Iron、日期和Analyst。细化诊断确认首轮片段选择是有效主动拒答，而不是错误JSON；后续另一次片段独立审核仍拒绝，最终原页视觉通路成功。只能说明调用波动及路径差异，不能替代同六题基准、不能称稳定改进。诊断仅记录协议布尔值和片段数量，不修改生产来源或审核。

当前评分暂估仍56—70，80分未证明。

## Round23完整SQL独立结果

完整56题已结束，源码稳定、模型runner未打开参考。冻结内联评分6/56；认证分页、原SQL重放评分48/56（85.71%），较Round22的46/56净增2题。两道此前COUNT日期字段物理投影失败的多轮题cs-s2-t2、cs-s2-t3均通过。多轮18/20、2组五轮全过，仍未达到历史Round18的20/20。不能把全部变动归因于单一补丁，不能用开发题替代官方成绩。

剩余8题：cs-c05物理来源；cs-f06独立语义审核拒绝；cs-f12、cs-r01、cs-r03必需读取来源；cs-r06完整结果；cs-s1-t5、cs-s3-t2澄清。失败全部保留。全量回归仍运行，不用上一版4617项替代本版证明。

终版完整回归现已结束：`LOCAL_REGRESSION_ROUND23_RECORD_COUNT_20261006.json` 4626 passed、0 failed、3 skipped、12 subtests passed、4既有警告，源码起止稳定。覆盖本版记录计数与分组来源补丁；不覆盖未来跨页比较的生产接入（目前只有独立tools原型）。
