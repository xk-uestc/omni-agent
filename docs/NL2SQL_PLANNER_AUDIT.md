# NL2SQL Planner Audit Contract

`/api/v1/nl2sql/query` 的 `plan.planner_audit` 和 `plan.intent_audit.planner_audit`
记录模型规划候选经过本地 Schema、外键、只读和意图完整性校验后的最终决策。
它不包含模型原始响应、令牌、数据库内容或 SQL 生成前的敏感输入。

## 字段

| 字段 | 含义 |
| --- | --- |
| `candidate_source` | 候选规划来源；当前外部模型为 `external_model` |
| `final_source` | 最终采用的规划来源：`model_validated` 或 `rules_fallback` |
| `fallback` | 是否从模型候选回退到规则规划 |
| `decision` | `accepted` 或 `rejected_or_unavailable` |
| `reason_type` | 异常类别；接受时不存在 |
| `reason_code` | 稳定的机器可读拒绝分类；接受时不存在 |
| `reason` | 面向审计人员的简短中文说明；不应作为程序分支条件 |

## 原因码

常见原因码包括：

- `low_confidence`：模型置信度低于门限。
- `ungrounded_metric` / `ungrounded_filter`：指标或过滤值没有问题依据。
- `missing_explicit_filter` / `missing_time_range`：遗漏用户明确条件。
- `missing_group_dimension` / `extra_dimension`：分组维度缺失或凭空增加。
- `time_grain_mismatch`：时间分组粒度不一致。
- `comparison_mode_mismatch` / `comparison_mismatch`：同比/环比口径或窗口不一致。
- `analysis_mode_mismatch` / `top_n_mismatch`：分析模式或 Top-N 不一致。
- `metric_aggregation_mismatch`：SUM/AVG/COUNT 等指标聚合口径不一致。
- `missing_having` / `extra_having` / `having_mismatch`：聚合阈值被遗漏、凭空增加或改写。
- `top_n_order_mismatch`：Top-N 的升序/降序方向不一致。
- `clarification_bypass`：绕过规则层明确要求的澄清。
- `provider_unavailable_or_invalid_response`：模型服务不可用或返回格式无效。
- `model_contract_or_grounding_rejected`：其他契约或依据校验失败。

## 使用要求

前端展示应优先使用 `decision`、`final_source` 和 `reason_code`；`reason` 仅用于人类可读详情。
评测脚本会在 summary 中聚合 `planner_rejection_reasons`。规则规划器独立运行时没有模型候选，
因此 `planner_sources` 可能只有 `rules`，这不代表模型链路被验证过。
