# ICT 赛题八结构化问数基线

本目录是第三届产业创新大赛赛题八的可回退实现边界。当前提供一个不依赖模型
密钥的、可复现的结构化问数基线，覆盖单表聚合和经 Schema 外键图验证的
受约束多表 JOIN 路径（自动补齐中间表，默认最多 4 跳）：

```text
自然语言问题
  -> Schema introspection + 业务指标目录
  -> 规则/真实模型提出 QueryPlan v2
  -> 字段/取值/外键 JOIN 与粒度校验
  -> 各事实表独立聚合 + 公式参数绑定
  -> 只读 SQL 安全门
  -> SQLite 执行
  -> SQL、数据行、字段映射、粒度审计、解释和来源溯源
```

## 运行

在仓库根目录执行：

```powershell
python ict-track8/scripts/create_demo_db.py
uvicorn backend.app:app --app-dir ict-track8 --host 127.0.0.1 --port 8020
```

行业级可复现数据包（订单、明细、产品、客户、区域、售后工单）可单独生成：

```powershell
python ict-track8/scripts/create_industry_demo_db.py
$env:ICT8_DB_PATH = "$(Get-Location)\ict-track8\data\industry_demo.sqlite"
$env:ICT8_SCHEMA_ALIASES = "$(Get-Location)\ict-track8\data\industry_aliases.json"
uvicorn backend.app:app --app-dir ict-track8 --host 127.0.0.1 --port 8020
```

该数据包不把表名、列名或外键关系写死在规划器中；规划器从 SQLite 元数据读取真实
Schema，并通过别名包识别业务表达，适合替换为比赛方数据库做迁移验收。

查询示例：

```powershell
curl.exe -X POST http://127.0.0.1:8020/api/v1/nl2sql/query `
  -H "Content-Type: application/json" `
  -d '{"question":"2025年华东地区的销售额是多少"}'
```

`/api/v1/agent/query` 支持可选 `session_id`。只有传入同一个 session 时才会把上一轮
问题作为上下文参与规划；不传则保持无状态。澄清选项提交时可将 `use_context` 设为
`false`，避免重复拼接已补全问题。会话最多保留 8 轮、空闲 30 分钟后过期；设置
`ICT8_SESSION_DB=D:\\path\\to\\ict8-sessions.sqlite` 后改用 SQLite 持久化，支持
多 worker 共享同一会话边界，
并在 trace 中同时记录原问题、改写问题和上下文轮数。

需要实时展示过程时使用 `/api/v1/agent/query/stream`；它按 `intent`、`structured_query`、
`document_retrieval`、`evidence_fusion` 顺序发送 SSE `trace` 事件，最后发送完整的
`done` 快照。阶段事件来自真实编排回调，不能由模型补写或替换。

跨源多跳示例：

```powershell
curl.exe -X POST http://127.0.0.1:8020/api/v1/agent/query `
  -H "Content-Type: application/json" `
  -d '{"question":"2025年华东地区的销售额政策"}'
```

响应中的 `structured` 保存真实 SQL、参数和数据行；除普通聚合外，带“排名/排行”会
生成可审计的 `DENSE_RANK()`，带“占比/份额/比例”会生成窗口总量百分比，并要求
明确分组维度。`document_evidence` 保存
文档片段和来源 URI，`trace` 保存意图、结构化查询、文档检索和证据融合四个阶段。
带“同比”的问题会比较指定年份/月份与上年同期，带“环比”的问题会比较指定月份与上月，
返回本期、同期/上期和变化百分比；没有明确周期时会主动澄清，不会使用当前日期猜测。
当结构化问题缺少指标、比较维度或时间范围时，响应会返回 `clarification_code`
和候选选项；前端选择后调用 `/api/v1/nl2sql/clarify`，服务端重新生成计划并再次
经过只读安全门。

独立的文本、图片和 PDF 质量检查接口已下线。资料上传仍经知识库入库流程处理；OCR、切片及来源证据服务于检索问答，不提供单独的模拟评分页面。
独立 Demo：

```powershell
.\scripts\start-track8.ps1
# 浏览器打开 http://127.0.0.1:8021
```

页面展示结构化 SQL、结果行、文档来源和四阶段执行 trace，不替代生产 `ragv6-ui`；
这样比赛演示和生产客服链路可以分别回退、验证和部署。

文档页级基准：

```powershell
python ict-track8/eval/bench_scale.py --repo ict-track8 --rows 1000 --pages 10 100 500 --repeats 5 --out eval-reports/ict8-document-analysis.json --fail-on-error
```

它只测本地文本/质量分析和 OCR 重试计划，不把外部 OCR、网络或模型耗时计入结果。

行业级结构化评测：

```powershell
python ict-track8/scripts/run_industry_eval.py
```

评测集覆盖多跳 JOIN、日期/值过滤、第二业务主题、窗口排名/占比、HAVING 子查询和
主动澄清；报告写入 `data/industry-eval-report.json`，失败时返回非零退出码。

接入正式数据库前先验收字段标注：

```powershell
python ict-track8/scripts/validate_schema_annotations.py `
  --database D:\path\to\readonly.sqlite `
  --aliases D:\path\to\schema-aliases.json
```

该检查会报告错表、错列、非法角色、冲突别名和未覆盖字段；未覆盖字段是迁移提醒，
冲突或不存在字段会以非零退出。运行服务后也可查看
`GET /api/v1/nl2sql/schema/annotation-report`。

构建可重建源码包（不含数据库、日志、缓存、备份和密钥）：

```powershell
python ict-track8/scripts/package_delivery.py --output dist/ict-track8-source.zip
```

压缩包内的 `MANIFEST.json` 记录每个文件的大小和 SHA-256；下载后可先核对清单，再按
README 的启动命令生成本地 SQLite 和评测报告。

下载后可在不解压、不执行项目代码的情况下校验 ZIP：

```powershell
python ict-track8/scripts/verify_package.py dist/ict-track8-source.zip
```

校验器会检查清单中的文件是否缺失、大小或 SHA-256 是否变化，并拒绝 ZIP 中未列入
清单的额外成员；返回非零退出码即表示包不可接受。

完整基线/候选回归的唯一执行源是 `eval/run_all.sh`。在 Git Bash 或 WSL 中运行：

```bash
bash ict-track8/eval/run_all.sh
BENCH=1 STRICT=1 bash ict-track8/eval/run_all.sh
```

Windows PowerShell 可调用 `eval/run_all.ps1` 作为转发包装器，但只有检测到
Git Bash/MSYS 或 WSL 环境时才执行；普通 PowerShell 会直接提示切换到 Git Bash/WSL，
不会静默跳过评测。包装器不维护第二套评测逻辑。

生产联调时可设置：

```text
ICT8_MANUAL_RETRIEVER_URL=http://127.0.0.1:8014
ICT8_MANUAL_RETRIEVER_TOKEN=<服务端 Bearer token>
ICT8_SCHEMA_ALIASES=D:\\path\\to\\schema-aliases.json
ICT8_DB_PATH=D:\\path\\to\\readonly.sqlite
ICT8_SESSION_DB=D:\\path\\to\\ict8-sessions.sqlite
ICT8_MANUAL_RETRIEVER_TIMEOUT=8
ICT8_MANUAL_RETRIEVER_RETRIES=1
ICT8_PLAN_URL=http://127.0.0.1:8090/plan
ICT8_PLAN_TOKEN=<服务端环境变量中的模型规划 token>
ICT8_PLAN_TIMEOUT=8
ICT8_PLAN_RETRIES=1
```

生产模式建议额外设置：

```text
ICT8_ENV=production
ICT8_API_TOKEN=<仅放在服务端环境变量中的 Bearer token>
ICT8_MAX_BODY_BYTES=33554432
```

生产模式下除 `/health` 外的接口必须携带 `Authorization: Bearer <ICT8_API_TOKEN>`；
未配置 token 会拒绝启动后的业务请求，错误 token 返回 401。所有 POST/PUT/PATCH
请求统一受 `ICT8_MAX_BODY_BYTES` 限制（默认 32 MiB，允许范围 1 KiB 到 64 MiB），
超限返回 413。模型规划和生产检索的远程 URL 在生产环境必须使用 HTTPS；仅允许
`localhost`、`127.0.0.1` 和 `::1` 的回环 HTTP 用于同机开发服务。不要把 token 写入
代码、前端或仓库。

两项同时存在时使用生产 `/retrieve`；否则使用版本化的小型 JSON 知识集。生产检索
不可用时接口返回 `partial`，保留 SQL 和数据来源，并在 `trace` 中标记降级，不会
用本地文档静默冒充生产证据。

生产适配器只对连接异常、429 和 5xx 做最多 3 次指数退避重试；401/403 不重试，
避免掩盖鉴权配置错误。可用 `python ict-track8/scripts/benchmark_retrieval_adapter.py`
在目标环境测量真实 HTTP 的健康、P50/P95、失败率和平均命中数。

`ICT8_SCHEMA_ALIASES` 是可选的 JSON 别名表；即使没有别名，规划器也会根据真实
字段名和 SQLite 类型建立基础候选，迁移到新业务库不需要复制示例表规则。

如需接入模型进行要素识别，可向 `Nl2SqlEngine` 注入 `model_plan_provider`。模型必须返回
版本为 `1` 或 `2` 的纯 JSON 计划；v2 支持多个事实指标、派生指标、单位/币种、缺失策略和
输出指标。`backend/nl2sql/model_contract.py` 会先验证标识符、聚合、过滤器、外键路径和
用户明确槽位，再由 `metric_compiler.py` 按事实原生粒度分别聚合，避免明细表互相放大，
最后复用只读安全门。模型不能直接提交 SQL，非法或含歧义的计划会被拒绝并留下审计原因。

`data/demo_metric_catalog.json` 是版本化业务口径示例。它把“客单价”等派生指标定义为
销售额/订单数，并记录单位、币种和配置哈希；缺失字段、零分母和口径未定义时系统会返回
未知或澄清，不会凭文档文字猜造数据。正式业务库应复制该结构并由业务方审核。

部署时也可以通过 `ICT8_PLAN_URL` 和 `ICT8_PLAN_TOKEN` 启用内置 HTTP 提供器。请求只发送
问题和 Schema 快照，不发送数据库内容、会话历史或密钥；模型服务超时、返回非法 JSON 或
计划不通过校验时，自动回退到规则规划，并在 `plan.planner_source` 标记
`rules_fallback`。`GET /health` 会显示启用的规划器模式和不含敏感值的配置告警。

需要使用 OpenAI-compatible Responses API 时配置：

```text
ICT8_PLAN_PROVIDER=responses
ICT8_OPENAI_BASE_URL=https://api.openai.com/v1
ICT8_OPENAI_API_KEY=<仅放服务端环境变量>
ICT8_OPENAI_MODEL=<明确的模型名称>
ICT8_OPENAI_REASONING=medium
```

`responses_provider.py` 使用严格 JSON Schema（`store=false`），模型只提出 v2 计划；响应中的
输入/输出 token、模型和思考强度会进入非敏感规划审计，API key 永不进入计划、日志或浏览器。
真实模型经由同一业务规则、单位、粒度、外键、覆盖率和只读执行门；不可用或被拒绝时才回退规则，
不能把回退结果标记为模型结果。

`data/knowledge_documents.json` 是 11 种文档类型的离线评测夹具（政策、服务规则、
指标定义、流程、字段说明、公式、FAQ、扫描质量、多语言和图片证据），用于可复现
测试；离线检索采用确定性的轻量 BM25（中文二/三字片段与标题加权），它不冒充生产
手册，生产联调应设置 `ICT8_MANUAL_RETRIEVER_URL`。

接口不会接受写入语句。每次响应都包含生成的 SQL、参数、字段链接、执行解释、
查询摘要和结构化数据来源。该模块不读取或写入生产 RAG 的密钥和运行数据库。

## 当前边界

这一批已经提供最小销售库和行业级示例库两条可回退路径，支持真实外键图约束的多跳
JOIN、文档证据融合和有界会话。它仍不冒充已经完成任意 SQL 语义、真实 OCR 执行、
正式比赛数据标注或生产 8014 在线验收；这些边界在需求矩阵中单独跟踪，并保留每个
阶段的 Git 回退点。
