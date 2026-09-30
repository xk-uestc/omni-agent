# ICT 赛题八技术实现说明

## 1. 目录

```text
ict-track8/backend/app.py                 FastAPI 入口与请求校验
ict-track8/backend/session.py             有界可选会话（内存或 SQLite）
ict-track8/backend/cross_source.py        SQL/文档编排、BM25、trace
ict-track8/backend/retrieval_adapter.py  生产 8014 HTTP 适配器
ict-track8/backend/nl2sql/                Schema、规划、执行与安全
ict-track8/backend/document_analysis.py   文档质量、公式、OCR 重试计划
ict-track8/backend/image_quality.py       图片探针与白名单预处理
ict-track8/backend/pdf_ingest.py          PDF 文本页/空页边界
ict-track8/frontend/                      独立可解释 Demo
ict-track8/tests/                         回归与契约测试
```

## 2. HTTP 契约

### `GET /health`

返回 `ok`、数据库文件名和当前文档源（`local_json` 或 `vnext_http`）。

### `POST /api/v1/agent/query`

请求：

```json
{
  "question": "按月统计 2025 年华东销售额",
  "top_k_documents": 4,
  "session_id": "optional-client-id",
  "use_context": true
}
```

返回包含 `status`、`answer`、`structured`、`document_evidence`、`trace`、
`latency_ms`、`effective_question` 和 `context_turns`。`status=partial` 表示生产
文档检索失败但结构化结果仍可用；`status=clarification` 表示不能安全规划。

### `POST /api/v1/agent/query/stream`

返回 `text/event-stream`。检索编排每完成一个阶段就发送一个 `trace` 事件，字段与
普通响应的 trace 项一致，并附带 `trace_id`；执行结束发送一个完整 `done` 事件，
包含普通查询的所有字段。客户端断开不会改变数据库只读策略或会话上限。

澄清接口 `POST /api/v1/nl2sql/clarify` 会把选择回填到原问题后重新走完整的跨源编排，
一次性返回新的结构化结果、文档证据和四阶段 trace，并在提供 `session_id` 时只写入
一条原问题到改写问题的会话 turn，避免前端重复提交造成上下文膨胀。

会话存储默认是进程内内存；生产部署可设置 `ICT8_SESSION_DB` 指向专用 SQLite 文件。
持久化模式使用会话元数据和顺序化 turn 表，按最多 512 个会话、每会话 8 轮和 30
分钟 TTL 做边界清理，多个 worker 可以共享同一文件。SQLite 文件只保存问题和改写问题，
不保存模型密钥、文档 token 或原始上传文件。

### 文档/图片/PDF

- `POST /api/v1/documents/analyze`：文本、页信号、目录、公式和 `ocr_retry_plan`。
- `POST /api/v1/documents/image-quality`：分辨率、亮度、对比度、边缘和建议。
- `POST /api/v1/documents/image-enhance`：仅执行白名单变换，返回 PNG、变换清单和
  `ocr_executed=false`。
- `POST /api/v1/documents/ocr`：调用配置的 HTTP OCR 或 Tesseract，返回每次尝试的变换、
  置信度、错误和最终状态；未配置执行器时返回 503。
- `GET /api/v1/documents/ocr/health`：只读检查 OCR 是否配置以及本地 Tesseract 是否可发现，
  不上传图片、不执行远程 OCR。
- `POST /api/v1/documents/pdf-analyze`：提取可读页，标记 `ocr_required_pages`，
  不伪造 OCR。

所有 Base64 输入均有大小限制；未知变换、越界裁剪、非法图片和非法 PDF 返回 400/413。

## 3. SQL 安全与溯源

1. `SchemaIntrospector` 只从 SQLite 元数据读取表、列、类型和外键。
2. `SingleTablePlanner` 只生成 SELECT 聚合，标识符统一引用，值使用 `?` 参数。
3. 外键图 BFS 产生可审计 `join_path`；找不到路径返回澄清而不是笛卡尔积，存在多条
   同长度路径时返回 `ambiguous_join_path` 选项，选择值只允许由服务端生成的路径签名。
4. `validate_read_only_sql` 和 SQLite authorizer 双重阻断写操作。
5. `QueryResult.provenance` 记录数据库、表、字段链接、行数和 query hash。
6. `analysis_mode=rank` 使用 `DENSE_RANK()`，`analysis_mode=share` 使用窗口总量计算
   百分比；两者都要求明确分组维度，避免对单个总数伪造排名或占比。
7. `comparison_mode=同比/环比` 使用参数化条件聚合，同时返回本期、同期/上期和变化百分比；
   同比必须指定年份或月份，环比必须指定月份，缺少周期时只返回澄清而不猜测。

### 3.1 模型规划器安全契约

外部模型可以通过 `Nl2SqlEngine(..., model_plan_provider=...)` 提供版本为 `1` 的 JSON
计划，但不能提交 SQL 或任意 JOIN 条件。`ModelPlanValidator` 会逐项校验：

- 表、指标列、维度列和过滤列必须存在于实时 Schema；
- 聚合函数、分析模式、变换、运算符和结果上限必须在白名单内；
- 数值聚合只能作用于数值列，日期变换只能作用于日期列；
- JOIN 只从 Schema 外键图重新推导；模型给出的路径若无法验证或存在歧义则拒绝；
- 通过校验后仍调用同一个参数化 SQL builder、只读 SQL 校验器和 SQLite authorizer。

因此模型输出只能改变“计划”，不能绕过只读执行门。验证后的结果会在
`plan.planner_source` 标记为 `model_validated`，便于对照评测和审计。

## 4. 生产联调

设置：

```text
ICT8_MANUAL_RETRIEVER_URL=http://127.0.0.1:8014
ICT8_MANUAL_RETRIEVER_TOKEN=<只放在服务端环境变量>
ICT8_SCHEMA_ALIASES=<可选 JSON 别名文件>
ICT8_PLAN_URL=<可选模型计划 HTTPS endpoint>
ICT8_PLAN_TOKEN=<只放在服务端环境变量>
```

适配器使用 Bearer token、JSON 请求和有限超时；连接错误、429 和 5xx 最多做受控退避
重试，401/403、畸形 JSON 或非法
证据项均转换为 `DocumentRetrievalError`，由编排层显式降级。token 不进入返回值和错误
消息。生产服务真实可用性必须在部署环境用独立健康检查和回归问题确认。

### 4.1 右侧审计兼容契约

`/api/v1/agent/query/stream` 在 `evidence_fusion` trace 之后、`done` 之前发送一个
`event: audit`。`backend/production_audit.py` 只映射 trace 中真实存在的字段：离线
JSON 检索器的 `bm25_raw`、`bm25_relative`、`retrieval_channel=bm25`、标题、摘要、
来源和证据角色会原样透传。其输出明确声明：

```json
{"channels":["bm25"],"availability":{"bm25":true,"dense":false,"rrf":false,"rerank":false}}
```

本地赛题八链路不会用有界展示分数冒充 BM25 原始分，也不会填充 Dense、RRF 或 rerank
字段；没有明确通道声明的远端证据不会被默认归类为 BM25。这些字段只在正式 8014 三路检索链路真实返回时展示。生产 UI 接入时应消费该
`audit_contract_version=1` 事件，并按 `availability` 控制通道徽标。

正式数据库迁移前使用 `scripts/validate_schema_annotations.py` 或
`GET /api/v1/nl2sql/schema/annotation-report` 验收别名文件；不存在的表/列、非法角色和
冲突别名是阻断错误，未覆盖字段作为明确警告保留在报告中。

模型计划服务的请求体为 `{question, schema}`，响应为计划对象或 `{plan: {...}}`。服务端
仅把它当作提议：HTTP 失败和非法计划均回退规则规划，随后仍经过 Schema/外键图校验、
参数化 SQL builder、只读 SQL 校验和 SQLite authorizer。模型 token 不写入日志或响应。

## 5. 启动与回退

```powershell
python ict-track8/scripts/create_demo_db.py
.\scripts\start-track8.ps1
python -m pytest ict-track8/tests -q
```

每个能力均为独立 Git 提交；回退前先查看：

```powershell
git log --oneline --decorate -- ict-track8 docs
git show <commit>
```

交付源码包使用 `scripts/package_delivery.py` 构建。它只收集 `ict-track8/` 与赛题八文档，
排除 SQLite、日志、缓存、备份和报告，并在压缩包根目录写入 `MANIFEST.json` 哈希清单；
这份源码包不包含正式数据库、模型密钥或外部 OCR 语言包，拿到后需要按环境变量说明接入。

禁止用 `reset --hard` 或清理其他目录的用户改动。当前分支上的历史生产目录脏改动与
赛题八提交分开管理。
