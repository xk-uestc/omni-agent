# ICT 赛题八需求追踪矩阵

本表只记录当前仓库中有代码和测试证据的状态；“部分”与“待完成”不能在答辩材料中
当作已完成。每个后续能力都应在对应 Git 提交中更新本表。

| 赛题能力 | 当前状态 | 代码/证据 | 尚缺内容 |
|---|---|---|---|
| 可交互问答 Demo | 部分（独立 UI + 有界 session + SSE） | `ict-track8/frontend/`、`backend/session.py`、`/agent/query/stream` | 接入正式生产 UI；SQLite 会话持久化已提供，可在部署环境验收 |
| 单表 NL2SQL | 已实现 | `ict-track8/backend/nl2sql/`、基础回归测试 | 正式比赛 Schema 标注 |
| SQL、数据、来源展示 | 已实现 | `QueryResult`、`CrossSourceAgent.trace`、`production_audit.py` | 正式 UI 仍需在目标环境验收 |
| 要素识别与问题改写 | 已实现（规则基线 + 可插拔 HTTP 模型计划 + 安全回退） | `planner.py`、`nl2sql/model_contract.py`、field links、行业 14 题评测、模型安全回归 | 目标模型/正式 Schema 校准 |
| 缺失维度主动澄清 | 已实现 | clarification code/options、HTTP 闭环测试 | 正式生产 UI 联调 |
| Schema Linking | 已实现（外键图多跳 + 歧义澄清 + 标注验收） | `schema.py`、`nl2sql/annotation.py`、别名包、多路径回归测试、行业 14 题评测 | 接入正式 Schema 后完成现场标注 |
| 多表 JOIN | 已实现（受约束多跳） | `planner.py`、`nl2sql/model_contract.py`、行业级 4 表路径测试、JOIN 选择回填 | 更复杂嵌套查询 |
| 单文档 RAG | 已实现（生产适配） | `retrieval_adapter.py` | 正式赛题文档集标注 |
| 结构化 + 文档联合查询 | 已实现（离线/HTTP） | `cross_source.py`、跨源测试、受控重试适配器 | 目标环境 8014 实时联调 |
| 多跳解释链 | 已实现（四阶段 trace + SSE 时间线 + audit 兼容事件） | `CrossSourceAgent.trace`、`production_audit.py`、`/agent/query/stream`、`frontend/app.js` | 与生产 8014 右侧审计栏统一字段验收 |
| 公式识别与计算 | 已实现（安全基线） | `document_analysis.py` | PDF 版面/公式 OCR 适配 |
| 非标准目录识别 | 已实现（规则基线） | `DocumentAnalyzer._headings` | 复杂 PDF 真实集评测 |
| 文档复杂度评估 | 已实现 | complexity metrics | 标注集校准阈值 |
| 文档质量综合评估 | 已实现（文本/页面/图片信号） | `document_analysis.py`、`image_quality.py`、`pdf_ingest.py` | 接入真实 OCR 置信度校准 |
| 低质量文档鲁棒性 | 部分（页级计划 + 可插拔 OCR 执行器 + 能力探针） | `DocumentAnalysis.ocr_retry_plan`、`/documents/ocr`、`/documents/ocr/health`、OCR 重试测试 | 目标环境 OCR 语言包和真实扫描集回归 |
| 性能与吞吐量报告 | 部分（结构化 + 本地文档页 + HTTP 基准脚本） | `ICT_TRACK8_PERFORMANCE.md`、三个 benchmark 脚本 | 真实 10/100/500 页 OCR、目标环境网络/模型数据 |
| 完整交付材料 | 部分（设计/技术说明 + 可重建源码包） | `ICT_TRACK8_DESIGN.md`、`ICT_TRACK8_TECHNICAL_SPEC.md`、`scripts/package_delivery.py`、`MANIFEST.json` | 答辩 PPT、正式数据集和最终提交包 |
