# 排名完整性、短答案与原生图表接线

## 已完成工程验收

活动项目为 D:/ICT8-OmniAgent。修改前完整恢复备份为
D:/ICT8-Backups/ICT8-complete-20261001-211650，2045 文件、595395471 字节，逐文件 SHA-256 PASS。
此备份不包含本文件以及之后的问答接线。

本轮完整回归为 **1692 passed / 0 failed / 1 Starlette warning / 12 subtests passed**，
pytest 66.51 秒，脚本总耗时 68.75 秒。机器报告为 TYPED_CHART_REGRESSION_20261001.json。
前端 knowledge.js 经 node --check；这不是浏览器交互验收。

## 程序变化

1. NL2SQL：模型将 top_n=1 与 limit=1 混用时，仅在同读快照独立原问题范围验证通过、
   无明确或未知行数限制时修正安全返回上限。排名与过滤不改，并列完整性门禁保留。
2. 普通文档：GroundedGenerator 完整事实通过后，typed_answer 独立绑定明确字段/角色/关系。
   KnowledgeStore 重读权威证据并重放证明后才返回 answer_projection。原 answer/claims 保留；
   范围、单位、正负号、原文定位和 SHA 均保留。不支持的复杂关系继续使用完整事实，
   不将短答案作为公式可信参数。
3. 原生折线图：由原 PDF 的明确数值标签提供值，轴拟合仅作几何校验，不以像素估计数字。
   仅支持有图框、明确年份、唯一颜色图例、完整标签序列和线性轴的原生折线图。
   模型读取真实原页图，只选 chart/series/year，必须与服务器问题绑定一致。
4. 主入口：严格表格路由无匹配后才进入原生图表路由，不覆盖表格范围冲突。
   在完整候选 PDF/page 范围内检查唯一性；时间/页数/大小限制失败则拒绝部分扫描授权。
   原生图表单位 unknown 保持可见，calculator_input_eligible=false。
5. 官方评测：原答案分数保持不变；短答案展示字段独立计分，未投影题在全题分母中计零。
   原生图表答案独立计数，不混入 model_grounded 的语言生成答案数。

## 效果边界

合成 PDF 用于测试几何边界和生产路由；官方原页只读解析用于确认数据结构，不能替代真实 API 验收。
代码冻结后启动 REAL_MODEL_NINTH_RUN_20261001.json 与 OHR_BENCH_TYPED_CHART_REPLAY_20261001.json；
在进程终止、报告及开始/结束源码 SHA 核验前，不声明新真实成绩。

仍缺复杂法律语义短答案、无边框/合并/跨页表格、跨列阅读、广泛图表类型、未知 SQL/RAG 高准确率证明。
本轮不制作 PPT/Word/PDF，未宣布全部赛题完成。

## 官方原题实测（终止且源码稳定）

OHR_BENCH_TYPED_CHART_REPLAY_20261001.json：12题/7原PDF，EM **2/12**，F1 **0.313369**。
6项语言生成实质回答、3模型拒答、2原生图表答案、1普通摘录；13次API均completed，
25642可见tokens，无失败/审计丢失。全部调用为 gpt-6-luna / medium。
两项chart原题均精确匹配，单位未声明仍明确unknown，不改原题/金标。
短答案投影命中0/12，不宣称其已带来官方效果提升。
上一轮同冻结题EM0/12、F1 0.136409；小样本已曝光开发回放，不能推断完整OHR泛化或比赛得分。

## 第九轮开发题（终止且源码稳定）

REAL_MODEL_NINTH_RUN_20261001.json：**39/44**；86调用、82completed、4failed、0审计丢失。
可见tokens下界400130，4次调用usage未知；非官方或盲测成绩。
失败为multiple_documents、cross-turn-2/3/4/5；SQL→文档和SQL五轮本次通过。
multiple_documents实际计划search携带document_id，但执行工具只接受query，属协议断层；
cross-turn-3本次直接失败是baseline SQL的missing策略与服务器契约冲突，错误码为
source_metric_missing_policy_mismatch。独立检查还发现其后置目标未绑定最终消费义务，
这是另一项缺口，不能冒称本次直接失败原因；cross-turn-4继承失败上下文。
API失败涉及sql-华南-2024-订单数（该题最终修复通过）、cross-turn-2两次SQL规划与cross-turn-5一次总规划。
即使规则回退数值正确，严格模型验收仍保留失败。

只读接线审查发现两项下一轮修复：缺少年份的相关chart问题提前回文字路径、
extract_pdf_charts的解析预算异常未转结构化incomplete。它们不改写本次冻结结果。
