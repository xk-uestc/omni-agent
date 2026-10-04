# ROUND12 非空字段计数实测

日期：2026-10-04。针对上一轮`cs-s4-t5`物理投影回归，使用生产NL2SQL引擎、项目配置的gpt-6-luna/medium和经官方数据清单验证的Sakila数据库，单独询问：

> 统计全部staff的picture非空记录数。

本次只把问题发送给模型；没有读取私有参考答案或评分器。

真实模型生成并通过独立审核：

```sql
SELECT COUNT("staff"."picture") AS "picture_non_null_count"
FROM "staff" AS "staff"
```

执行结果：`picture_non_null_count = 0`。模型调用两次：SQL提案与独立语义审核，均HTTP成功且`model_verified=true`。

同一修复的离线端到端夹具使用两条非空picture和一条NULL记录，实际回放为2，SQL直接COUNT picture，未用`COUNT(*) WHERE picture IS NOT NULL`替代。空表同样依赖SQLite `COUNT(column)` 返回单行0的原生语义。

回归：Round12相关专项 **99 passed**；全量本地回归 **3397 passed、0 failed、2 skipped、2 warnings、12 subtests**。完整Round12固定56题模型复测另存于`sakila-round5-cross-schema-20261002/runs/round12-final-development-20261004`，其分数报告生成后补入本轮总报告。

限制：该探针只验证一个真实问题和当前Sakila源；不单独证明整套SQL效果提升。它是来源计数血缘修复，不代表图片字段的业务含义，也不改变评估器合同。
