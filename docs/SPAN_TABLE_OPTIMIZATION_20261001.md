# 官方文档问答低分优化

## 已采纳的程序改动

- `grounded_span_answer.py`：完整有依据回答保留在 `full_fact_answer`，确定性投影优先；不能确定性投影时，用原文字面短答案选择和独立完整问题复核。短答案不能作为已证明的物理计算参数。
- `native_text_tables.py`：从原 PDF 原生文字坐标识别有明确行列关系的无框数字表及货币列表，保留页、坐标、原值、原件 SHA；不推断币种或金额倍率。
- `native_table_question.py`：模型选择事实、独立复核期间与条件，服务器计算 lookup/sum/ratio。拒绝跨表、跨期间、总计重复加组件、零分母及非终止小数。失败继续完整证据链或明确拒答。
- 前端区分确定性结果和模型复核结果，展示完整事实、范围与计算过程。
- 官方评测只评分真正输出的 `answer`；全文答案独立记录，不替换主成绩。API、失败、tokens 和源码起止指纹保留。
- `nl2sql/engine.py`：在实际读快照中重新抽取原始来源约束，再将排名、筛选、比较等 typed 槽位提供给 SQL 模型，避免上游任务改写丢失“第一名”。真实模型计划仍独立生成并严格核验；不自动改 top_n、不以规则回退冒称模型通过。

## 固定源码真实结果

只调用 `gpt-6-luna`，medium。以下真实模型报告属于算术精度加固前的冻结源码，后续算术修补不改写这些历史报告。

| 范围 | 精确匹配 EM | token F1 | 证据 |
|---|---:|---:|---|
| 历史 12 题基线 | 2/12 | 0.313369 | OHR_BENCH_TYPED_CHART_REPLAY_20261001.json |
| 本轮 12 题最终回放 | 4/12 | 0.438393 | OHR_BENCH_SPAN_TABLE_FINAL_REPLAY_20261001.json |
| 第一组新 10 题首次 | 0/10 | 0.014286 | OHR_BENCH_HOLDOUT_FIRST_20261001.json |
| 同 10 题修改后回放 | 4/10 | 0.414286 | OHR_BENCH_HOLDOUT_REPLAY_20261001.json |
| 第二组新 9 题首次 | 3/9 | 0.409351 | OHR_BENCH_HOLDOUT_SECOND_FIRST_20261001.json |
| 合成开发题第十轮 | 42/44 | 不适用 | REAL_MODEL_TENTH_RUN_20261001.json |
| 合成开发题第十一轮 | 43/44 | 不适用 | REAL_MODEL_ELEVENTH_RUN_20261001.json |

12 题 F1 相对历史基线增加 0.125024，EM 增加 2 题。10 题回放已曝光，不是盲测；9 题为排除前 22 题和重复问句后冻结的新 query，同文档曾曝光，不是新文档盲测。资源偏置小样本不能推算官方全量准确率，更不能换算比赛分数。

第十一轮 108 次 API 全部 completed，无丢失，441912 reported tokens；源码起止稳定。唯一失败 `sql_to_document` 为 `source_aggregation_mismatch`，SQL 来源约束拒绝了实际排名规划；API 成功不等于业务题通过。第十轮另一 `formula_sql` 失败同样保留，不以第十一轮通过覆盖历史失败。

修补原始来源模型上下文后的独立定向真实测试 **2/2**（`sql_to_document`、`formula_sql`），5 次调用均 completed、19924 tokens、源码起止一致，见 `REAL_MODEL_SOURCE_CONTEXT_TARGETED_20261001.json`。这不是修补后 44 题全量重测，不将 43/44 与定向通过拼成 44/44。

## 验证与交付

精度审查发现旧 Decimal 精度只按有效数字估算，极大整数与极小小数相加可能丢尾数。本轮已修复指数跨度、进位和相消，lookup 不做无谓求和。原 PDF SHA 与选中事实逐项重新核验后，7 个历史成功原生表格结果全部精确复算通过，见 `NATIVE_ARITHMETIC_FINAL_REPLAY_20261001.json`；无模型调用，不使用 gold，不重复语义复核。最终回归另存独立报告，不冒称重新执行了全部真实 API。

修补后完整本地回归 **1890 passed、0 failed、1 Starlette 弃用告警、12 subtests passed**，pytest 74.22 秒，报告脚本 76.25 秒，见 `SPAN_TABLE_SOURCE_CONTEXT_FINAL_REGRESSION_20261001.json`。单测不是模型准确率。

历史程序包在修正验收工具的 Windows USERNAME 环境后，独立目录 12 项 HTTP 检查全部通过，见 `PACKAGE_SMOKE_HISTORICAL_SEARCH_TARGET_V2_OS_USERNAME.json`。其它失败报告保留。这只能证明历史包；本轮新包另外验收。

本轮新程序包 `D:/ICT8-Backups/ICT8-program-20261001-span-table-source-context.zip`，513 文件、60588026 字节，SHA-256 `e2d0244462364cbb29e5f62ce38b180fddac41d9906ab94abbe9ed79e66a5641`。逐文件 manifest 与凭据排除核验通过；D 盘独立新目录启动的 12 项实际 HTTP 检查全部通过，见包生成后单独保存的 `PACKAGE_SMOKE_SPAN_TABLE_SOURCE_CONTEXT_20261001.json`。包含本地 dense 权重与小型公开资产，不含运行凭据或外置大型官方数据；使用现场 Python 依赖，不称洁净 OS 验收。

## 仍未解决

- 复杂多栏表、原文未明确的倍率、图表多步合计/差值及长文多跳仍低分。
- 原文字面存在和模型复核不等于形式语义证明；语义选择仍有模型不稳定性。
- 实测只覆盖少量官方 query 和已曝光开发题，未证明赛题全部指标达标。
- 程序包带本地 dense 权重和小型公开资产；大型官方原件外置，完整恢复备份另存 D 盘。包验收沿用现场依赖，不是洁净操作系统安装证明。

总体任务继续进行，不标记赛题全部完成。
