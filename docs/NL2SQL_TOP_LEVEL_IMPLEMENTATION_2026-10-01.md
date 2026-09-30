# ICT8 NL2SQL 本轮实现与验收记录

日期：2026-10-01。范围只包含 NL2SQL；RAG 检索、OCR 运行时和生产 8014 不在本轮修改范围。

## 已实现

1. `QueryPlan` 增加多个源指标、派生指标、单位/币种、缺失策略、输出指标、粒度审计和语义审计。
2. 新增 `metric_compiler.py`：每个事实表先聚合，再按共享维度合并；子表过滤使用事实主键半连接；禁止未经分摊定义的事实到多值维度金额汇总；支持确定性四则公式、零分母、单位/币种校验、Top-N、占比和结果限制。
3. Schema 读取复合外键、主键/唯一键和约束序号。只有目标键确实唯一时才把关系证明为 many-to-one；复合外键生成完整 AND 连接条件。
4. 新增 `semantics.py` 和 `demo_metric_catalog.json`：版本化指标目录、别名、依赖闭包、公式和配置 SHA-256。演示验证了客单价=销售额/订单数。
5. 新增 `responses_provider.py`：Responses API 严格 JSON Schema、`store=false`、明确模型和 reasoning effort；模型不能提交 SQL，所有计划经过本地验证和安全执行门。
6. 评测严格按结果列名、输出投影、单位/币种和澄清 code 判定；支持 `split_group`，并区分唯一 case、唯一问句和模板族；错误澄清不再算正确。
7. 前端审计响应新增结果单元格 SQL 定位、指标粒度和语义配置摘要所需字段；原有前端未提交改动继续保留。

## 实测证据

```text
python -m pytest tests -q
333 passed, 1 warning

python eval/run_eval.py --repo . --out data/nl2sql-strict-after-20261001.json
271 units
EX_answerable       1.0000
EX_simple            1.0000
EX_complex          1.0000
silent_error_rate   0.0000
clarification       1.0000 recall / 1.0000 precision
dialogue_success    1.0000 (5 dialogues)
safety_violations   0
database_modified   []
```

上述数字只证明当前受控评测集和演示数据库，不代表未知业务、真实扫描文件或组委会测试集成绩。当前评测共有 99 个 case、91 个唯一问句，多个数据变体共享题型；正式冲奖仍需按报告中的数据库/模板族隔离盲测。

真实模型也做了端到端调用验证。使用 `gpt-5.6-terra`、medium reasoning 时，以下三类问题均返回 `planner_source=model_validated`，并记录了 token 审计：

```text
2025年华东地区的销售额
2025年按地区统计销售额和订单数
2025年各地区客单价
```

模型产生的计划仍由本地检查字段、取值、时间、关联粒度、公式、单位和只读 SQL；测试中没有把模型原文或凭据写入响应。

## 交付边界

真实生产使用前应补充有业务方确认的指标目录、退款/成本等事实表和单位转换规则；当前演示库不含这些事实，系统应澄清或拒答。复杂多指标同比/环比暂要求逐指标时间角色，避免用一个时间窗口误套所有事实。
