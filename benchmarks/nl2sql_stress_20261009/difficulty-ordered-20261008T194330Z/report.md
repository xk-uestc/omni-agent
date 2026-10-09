# NL2SQL 持续压力测试报告

## 结果分类与重复失败

- 测试窗口：2026-10-08T19:43:30.559388+00:00 至 2026-10-08T19:59:38.585843+00:00（UTC）。
- 请求：242；正确：192；未通过：50；正确率：79.34%。
- 端到端延迟：P50 193.66 ms，P95 23970.1 ms，P99 34887.15 ms，最大 40012.48 ms。
- 范围：本项目演示数据库 HTTP 接口；gold SQL 仅本地评分，未发送给服务。

| 结果 | 次数 | 占比 |
|---|---:|---:|
| SQL 正确执行 | 192 | 79.34% |
| 澄清后未直接回答 | 32 | 13.22% |
| 错误 SQL/结果 | 17 | 7.02% |
| 超时/传输/服务错误 | 1 | 0.41% |
| 其他失败 | 0 | 0.00% |

- 首次覆盖唯一用例：242；首次结果分类：`{'correct_execution': 192, 'incorrect_execution': 17, 'technical_failure': 1, 'clarification_unanswered': 32}`。
- 未通过的唯一题目：50；跨轮重复失败题：0。
- 每轮难度顺序：`符合递增`；观测到的最大问答并发：1。

### 按难度统计

| 难度 | 正确 | 澄清 | 错误结果 | 技术失败 | 总数 | 正确率 | P50(ms) | P95(ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 27 | 0 | 0 | 0 | 27 | 100.00% | 192.72 | 338.5 |
| 2 | 138 | 8 | 17 | 1 | 164 | 84.15% | 193.45 | 22857.11 |
| 3 | 27 | 24 | 0 | 0 | 51 | 52.94% | 7883.39 | 26754.14 |

### 规划耗时

- 本地规则路径：191 次。

| 模型 | 调用数 | P50(ms) | P95(ms) |
|---|---:|---:|---:|
| gpt-5.5 | 9 | 13110.57 | 17887.22 |
| gpt-5.6-sol | 9 | 11659.46 | 25888.0 |

| API 来源 | 尝试数 | P50(ms) | P95(ms) |
|---|---:|---:|---:|
| code.conpera.ai | 27 | 11659.3 | 23379.69 |
| spacetimeai.cc | 27 | 10801.37 | 17887.02 |

### 高频未通过题

| ID | 问题 | 类别 | 失败次数 | P50等待(ms) | 最长等待(ms) |
|---|---|---|---:|---:|---:|
| catalog-campaign_sales-00 | 2025年广告归因销售额 | catalog_alias | 1 | 40012.48 | 40012.476 |
| variant-legacy-05-0-purchase_amount-00 | 2024年华东地区进货金额 | vocabulary_variant | 1 | 35051.6 | 35051.595 |
| catalog-campaign_impressions-01 | 2025年营销曝光量 | catalog_alias | 1 | 34887.15 | 34887.152 |
| group-bonus_amount-department | 2025年按部门分别统计奖金总额 | grouping | 1 | 34412.22 | 34412.218 |
| catalog-sales_target-01 | 2025年目标销售额 | catalog_alias | 1 | 33335.71 | 33335.713 |
| catalog-campaign_impressions-00 | 2025年广告曝光量 | catalog_alias | 1 | 30758.72 | 30758.715 |
| group-bonus_amount-region | 2025年按地区分别统计奖金总额 | grouping | 1 | 29909.59 | 29909.591 |
| catalog-sales_target-00 | 2025年销售目标 | catalog_alias | 1 | 26379.23 | 26379.227 |
| catalog-campaign_conversions-00 | 2025年营销转化数 | catalog_alias | 1 | 25058.56 | 25058.56 |
| group-campaign_impressions-campaign_type | 2025年按营销类型分别统计广告曝光量 | grouping | 1 | 24345.73 | 24345.731 |
| catalog-campaign_budget-02 | 2025年营销投入 | catalog_alias | 1 | 23970.1 | 23970.101 |
| variant-legacy-03-0-shipping_cost-02 | 2024年华东地区配送成本 | vocabulary_variant | 1 | 22857.11 | 22857.113 |
| group-expense_amount-region | 2025年按地区分别统计费用金额 | grouping | 1 | 21946.69 | 21946.689 |
| catalog-campaign_sales-01 | 2025年营销归因销售额 | catalog_alias | 1 | 21630.99 | 21630.992 |
| group-campaign_budget-channel | 2025年按渠道分别统计营销预算 | grouping | 1 | 21448.42 | 21448.425 |
| catalog-campaign_budget-00 | 2025年营销预算 | catalog_alias | 1 | 21329.84 | 21329.841 |
| catalog-campaign_clicks-01 | 2025年营销点击量 | catalog_alias | 1 | 19735.3 | 19735.301 |
| group-campaign_conversions-channel | 2025年按渠道分别统计营销转化数 | grouping | 1 | 18650.56 | 18650.565 |
| catalog-campaign_budget-01 | 2025年广告投入 | catalog_alias | 1 | 18557.53 | 18557.525 |
| group-discount_amount-region | 2025年按地区分别统计优惠金额 | grouping | 1 | 18066.12 | 18066.124 |
| group-first_response_minutes-issue_type | 2025年按问题类型分别统计平均首次响应分钟 | grouping | 1 | 17190.24 | 17190.237 |
| catalog-inventory_quantity-00 | 2025年库存量 | catalog_alias | 1 | 16814.5 | 16814.504 |
| variant-legacy-03-1-shipping_cost-02 | 2025年华东地区配送成本 | vocabulary_variant | 1 | 16685.4 | 16685.4 |
| group-gross_profit-product_category | 2025年按产品类别分别统计毛利 | grouping | 1 | 16015.59 | 16015.593 |
| group-campaign_budget-campaign_type | 2025年按营销类型分别统计营销预算 | grouping | 1 | 15875.46 | 15875.464 |
| group-campaign_clicks-channel | 2025年按渠道分别统计广告点击量 | grouping | 1 | 15229.98 | 15229.98 |
| group-campaign_clicks-campaign_type | 2025年按营销类型分别统计广告点击量 | grouping | 1 | 14455.46 | 14455.456 |
| group-first_response_minutes-priority | 2025年按优先级分别统计平均首次响应分钟 | grouping | 1 | 14416.35 | 14416.349 |
| group-campaign_impressions-channel | 2025年按渠道分别统计广告曝光量 | grouping | 1 | 13666.7 | 13666.698 |
| catalog-inventory_value-01 | 2025年库存价值 | catalog_alias | 1 | 13644.25 | 13644.246 |

### 服务资源

- 采样点：63，CPU 增量 70.219 秒，平均占用 0.0725 个核心。
- 工作集峰值 605.76 MB，私有内存峰值 3952.19 MB，线程峰值 106。
- 资源采样健康状态：`{'200': 63}`。

以上“澄清后未直接回答”表示该用例有确定的 scorer SQL，但系统选择追问；它不等同于执行了错误 SQL。
