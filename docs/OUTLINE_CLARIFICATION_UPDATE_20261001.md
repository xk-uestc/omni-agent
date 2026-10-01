# 实际 PDF 目录与统一澄清闭环更新

本轮仅修改 E 盘独立项目。D 盘原项目、迁移基线、SSH 与 Cloudflare 配置均未改动。没有再次调用真实模型 API；指定模型仍仅为 gpt-6-luna，此前鉴权 401 的证据保留。

## PDF 目录恢复

8 份实际 PDF、9 页，覆盖数字同级、中文章节、Markdown、中文括号、【】启发式、缺级、跨页继承以及金额不应成为标题。

- 首次运行 2/8：`PDF_OUTLINE_FIRST_RUN.json` 保留原始失败，不覆盖。
- 修复后 8/8：`PDF_OUTLINE_REPORT.json`。
- 金标 JSON SHA-256：`5ec1d160646af4679b8971775f933b5212ee3d7380f3b57dfbdfb9f5913254bc`。
- 金标与实际 PDF 哈希均冻结，重跑时变化会拒绝直接比较。
- 9 页正文实际渲染已核对：`evidence/pdf-outline-review.png`。

修复了同级标题被错误嵌套、缺级后回退错误、括号标题遗漏、正文断行合并跨越标题及金额误识别。层级跳跃和启发式标题保留告警。自编金标属于开发审计；修复后是开发回归，不是盲测或公开基准。

## 统一问答澄清

新增 `/api/v1/omni/clarify`，与统一问答共享结构化会话。客户端选择须匹配服务端实际提供选项；伪造标签不改变指标，过时选项返回 409。所有选择仍经过 NL2SQL 安全与语义门。

| 问题 | 修复后的行为 | 证据 |
|---|---|---|
| 页面只显示澄清文字 | 指标、取值、字段角色提供真实按钮 | 实际浏览器与接口回归 |
| 不同表“客户数”混在一起 | 区分 sales_orders.customer_id 与 customers.customer_id | 两个真实字段选择；客户主档无时间列时拒绝编造 |
| 单价默认求和 | Demo 单价契约为每条订单等权 AVG；显式合计仍 SUM | 华东 2025 平均 2265.666…；五个指标独立 SQL 金标 |
| 年/月选择无法填时间 | 支持年份与月份输入，验证 Gregorian 日期 | 同比、环比及非法时间回归 |
| 指定年份的趋势退化为总计 | 继续要求月/年粒度；显式按年优先 | 时间与粒度两层澄清、实际 GROUP BY 金标 |
| 趋势按销售额排序导致时间乱序 | 单/多指标趋势按时间升序；显式排名继续按指标排序 | 四类趋势实际SQL金标与显式排名回归，浏览器核对1月至8月顺序 |
| 页面刷新失去会话 | sessionStorage 保留会话 ID；新对话换 ID | 重载后“那华南呢”保留年份和平均单价，结果 2799 |

AVG 是演示库明示口径，不是数量加权均价。加权均价仍需澄清，不假称已经支持。页面重载保留服务端会话条件，不恢复整段历史消息展示。

## 验证

- 完整本地回归：455 passed，1 项 Starlette 弃用警告。
- 统一澄清与趋势新增 32 项，实际 PDF 新增 8 项。
- 原 15 份资料、6 格式、298863 字节重新入库，原文件哈希不变；QA 11/11、SQL 12/12、跨源五流程 5/5，引用逐字核对通过。
- 浏览器验证平均单价、重载追问、同比时间编辑、趋势两层澄清和新对话隔离，记录见 `OMNI_BROWSER_CLARIFICATION_REPORT.json`。

```powershell
python -m pytest ict-track8/tests -q
python tools/evaluate_pdf_outline.py
python ict-track8/scripts/package_delivery.py --with-public-assets --output dist/ict8-outline-clarification-20261001.zip
python tools/verify_checkpoint.py dist/ict8-outline-clarification-20261001.zip
```

验包在新临时目录重建、入库、启动服务，检查澄清平均值、后续追问、趋势粒度和实际 PDF 冻结金标；不复制凭据，也不调用模型。

## 仍需完成

真实模型 401 未解决，不能宣称真实模型效果已通过。独立未知任务、模型多跳五轮和端到端性能待验证。正式 Word/PDF/PPT 仍是 ade1b60 材料快照，原 SOURCE_MANIFEST 保留旧哈希；本轮新增报告不冒充材料已更新。最终提交前需重生成正式材料并迁移官方模板。项目整体保持未全面验收。
