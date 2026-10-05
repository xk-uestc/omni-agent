# 第十三至十五轮：NULL 计数、确认型追问与文档范围验证

日期：2026-10-04。此记录汇总继续优化中的三个方向。Round15 是已完成的完整真实模型回放；之后又收紧了 NULL 条件聚合守卫和确认型追问的残余文本守卫，因此该分数不是最终工作树的分数。本轮候选整体未达到 Round12 基线，不提交、不推送。

## 已实现

- 复杂 SQL NULL 计数：从限定字段和查询 AST 解析空值行计数；显式空值统计要求 SQL 有同字段 `IS NULL` 范围，不能错误地用 `COUNT(nullable_field)`。允许一条查询同时返回 `COUNT(*)`、`COUNT(field)`、空值计数与比例，但空值条件必须在 `CASE`/`FILTER` 中精确等于该字段的 NULL 谓词；附加条件可能漏数时拒绝。仍保留一次结构修复和独立模型审核。
- 连续对话：一个成功 SQL 之后，若追问明确替换已有字段值并确认沿用原指标，服务端校验保存的 SQL 摘要、来源修订、原指标、单一筛选值及重解析后的其余范围，再继承上下文。无可验证成功 SQL、复合变更、额外排除、指标/字段变化都不走该窄路径。
- 复杂文档：加入 PDF 声明页范围与原生严格网格表来源清单原型，绑定 PDF SHA、页码、行列、单元格坐标和原生文字块。该原型不接生产路由，仅覆盖已声明页面上的原生严格网格表；无框表、OCR、图表、跨页记录闭包仍未解决。

## 测试与实测

| 验收 | 结果 |
|---|---:|
| 定向计数、会话、追问范围测试（最终工作树） | **72 passed** |
| 完整本地回归（D 盘 pytest 临时目录，最后两条更严格守卫之前） | **3423 passed、2 skipped、12 subtests、1 个既有弃用警告** |
| Round12 artifact-delivery 开发集 | **48/56** |
| Round13 artifact-delivery 开发集 | **47/56** |
| Round14 artifact-delivery 开发集 | **44/56** |
| Round15 artifact-delivery 开发集 | **41/56**，会话18/20轮、2/4段完整通过 |
| 正确 Sakila 快照的定向真实 API | NULL 比例题通过，两个店各返回一个结果；模型为 gpt-6-luna/medium、HTTP 200 且已验证 |

这些 Sakila 题目是已曝光的开发回放，不是官方盲测。Round13/14/15 artifact-delivery 报告分别见 `SAKILA_ROUND13_ARTIFACT_DELIVERY_20261004.json`、`SAKILA_ROUND14_ARTIFACT_DELIVERY_20261004.json`、`SAKILA_ROUND15_ARTIFACT_DELIVERY_20261004.json`；它们与旧 inline 合同不可混比。

Round15 的模型运行清单记录 `complex_query.py` SHA 为 `0036a8bbb45bbacf287d1a75cb39447bf263068fed51ea83734608e38744d440`、`sql_history_scope.py` SHA 为 `51a2fe7b70ec2fa3f6ad767a72cb6937d6c4ce20833f109f40ac94d999a2d513`。最终工作树把条件聚合的 NULL 谓词从“含有 NULL 合取项”收紧为“整个条件就是 NULL 谓词”（最终 SHA `3f0630017bf053d1e15fa8bf93e2ee31eca7a5f7f2fdd9567b2eaac79fc63f4a`），并要求确认型追问的上下文改写残余只能是明确确认词（最终 `sql_history_scope.py` SHA `8d383c9a95326b94f4c13aca1915052216579991d2de0735d820732521be0958`）。因此41/56只属于Round15源码；最终工作树相关72项定向测试通过，但完整本地回归和56题真实模型评测均未在这两条最终守卫上重跑。

校验过的数据库为 `D:\ICT8-OfficialDatasets\sakila-round5-cross-schema-20261002\validated\sakila.sqlite`，SHA-256 `06f8e4374a04341bd66c2f11e2d6c131c36a6775697fcbe5ad6bdd615abd56ac`。一次误指向轻量占位库的 0 行探针已废弃，不计证据。正确数据库上的 NULL 计数定向 API 调用当次遇到无 HTTP 状态的传输失败；Round15 完整回放中的 `cs-f07` 曾成功通过，但不是最终严格守卫版本的复测。

## 当前结论与限制

- 计数和会话的确定性守卫测试通过，Round15 中 `cs-f07`、`cs-r07` 以及部分短追问恢复；但整题分数连续从48降到47、44、41，其他题受模型澄清、物理投影及来源校验影响，不能宣称总体准确率提高或采纳这一优化版本。
- 最终源码只做了 NULL 谓词精确性收紧，其 3423 项本地回归通过；最终源码还没有新的56题全量真实模型分数。
- 文档范围原型测试覆盖合成 PDF。对 OHR 实际 PDF 的两页扫描未发现严格网格表，正确结果是 `incomplete`；原型未接生产，不代表复杂文档目标完成。
- 先前一次全量测试把临时文件写入已近满的 C 盘，出现磁盘满错误；没有清理系统临时目录。随后改到 D 盘隔离目录的完整回归通过。
- 工作树仍是未提交候选。由于固定开发集退步且复杂文档尚未生产接入，不提交、不推送，不替换现有 GitHub 版本。
