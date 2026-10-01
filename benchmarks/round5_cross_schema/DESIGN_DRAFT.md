# 第五轮跨业务评测初始设计草稿（历史记录，当前状态见 README）

状态：方案草稿，尚未下载新样本、建立新 SQLite、生成未见题或调用模型。本文不含题目、reference SQL 或期望答案。当前运行的 RAG 评测与 backend 均不改动。

## 已确认的曝光边界

现有 `D:/ICT8-OfficialDatasets/adventureworks/adventureworks-subset.sqlite` 是微软官方 CSV 的三表 SQLite 移植，不是原生 SQL Server/PostgreSQL。`Customer`、`SalesOrderHeader`、`SalesTerritory` 共 51,295 行、43 个字段、3 个内部外键，三张表均为单列主键。

2026-10-01 的首测、qualification replay、第二轮报告分别为 0/6、0/6、4/6，使用同一题目 SHA 和数据库 SHA。随后资格解析修复与同题回归已有明确记录。该 Schema 与六道题只能标记为开发评测，不能重新称作未知 Schema 首测。当前 production 未发现 AdventureWorks 表名专门常量；数据移植工具的固定三表及计算列校验与通用实现修复是不同事实。

原库大结果问题仍需保留：两题应返回 19,119 个客户分组，当前只返回 100 行。前 100 行正确不能判完整结果通过。实际缺失值分布也有区别：`Customer.PersonID`、`StoreID` 有 NULL；`SalesOrderHeader.ShipDate` 声明 nullable 但当前数据没有 NULL，`Comment` 则全为 NULL。不得仅凭 DDL nullable 把无真实缺失的数据当作 NULL 能力覆盖。

## 样本选择

| 分层 | 候选 | 使用边界 |
|---|---|---|
| 开发回归 | 现有 AdventureWorks 三表与六道冻题 | 原失败继续保留，独立报告，不并入新 Schema 分数 |
| 优先的新来源 | Microsoft `sql-server-samples` 的 WideWorldImporters OLTP 公共样本，销售、采购、库存范围 | 当前源码、文档、benchmarks 检索未见使用记录；尚未获取或建立实际 SQLite，不能声称已冻结可运行 |
| 可快速扩展但较弱的隔离 | 同一已固定 Microsoft commit 的 AdventureWorks 采购、生产、人事原始 CSV | 这些实际 SQLite 表范围尚未在现有样本中出现，但完整源 DDL 已于此前下载；只能称新表范围迁移首测，不能称绝对未见来源/DDL |

优先选择独立 WideWorldImporters 样本，先固定官方 release/commit、许可证、原资产 SHA、导出命令和版本，再以只读 SQL Server 导出生成可追溯 SQLite。必须保留选定字段、原始行、PK、复合键、全部内部 FK，逐项记录外部 FK 和 SQL Server 类型/时态/计算列转换边界。官方备份、具体下载 URL 和 SQLite 转换尚未验证；不得借第三方 SQLite 文件替代后称微软原生数据库。

若来源获取代价过高，AdventureWorks 新表范围可作为另一个明确标记的开发/迁移层。候选采购包含 Vendor、PurchaseOrderHeader、PurchaseOrderDetail、ProductVendor 及关系目标；生产包含 Product、ProductInventory、Location、WorkOrder、WorkOrderRouting；人事包含 Employee、Department、Shift、EmployeeDepartmentHistory、EmployeePayHistory。源 DDL 已证明这些范围存在复合主键、多日期角色和 nullable 字段，但 CSV 实际数据、SQLite 转换与行数均待核验。不要复用只支持 Sales 的现有导入函数后悄悄遗漏新 Schema。

## 固定题量与难度配额

建议新来源主层 36 道独立问数，三个业务范围各 12 道；另外 4 段固定五轮会话，共 20 turn。主层固定分母 56 turn，同时报告独立问数准确率、逐轮准确率和整段全通过率。旧 AdventureWorks 六道题单列，不补入新来源分母。

六类独立问数各 6 道、每类三个业务范围各 2 道；允许交叉标签，但每题只归一个配额主类。配额在第一次被测运行前冻结，不能按当前 PLAN_SCHEMA 支持范围裁掉难题。

| 主类 | 必须覆盖 |
|---|---|
| 复杂聚合 | 多指标不同粒度、去重口径、条件聚合、加权而非行级平均、聚合后阈值、并列排名 |
| 嵌套与集合 | 分组聚合后的二层比较、相关 EXISTS/NOT EXISTS、反连接、每实体最新记录；至少两题超出当前仅 scalar_avg 的表达范围 |
| 多表与关系 | 至少三表 JOIN、头/明细扇出、两个事实表独立预聚合、复合键、可选关系保留、关系角色明确 |
| 缺失值 | COUNT(*) 与 COUNT(column)、AVG 忽略 NULL、全 NULL 聚合、NULL 与 0 区分、NOT IN 与 NULL 的陷阱、无匹配实体 |
| 完整结果 | 无用户 LIMIT 的完整分组/明细、至少两题真实结果超过 100 行、重复行多重集、显式 Top-N 含并列、排序、多列投影 |
| 时间与业务口径 | 不同日期角色、半开时间范围、跨年边界、历史有效区间、最新状态与历史记录、币种/单位按来源保留 |

源数据剖析可以确认题目数学可解、有真实非空或缺失边界，但不能先调用被测引擎筛选“能通过的题”。合法空结果是单独标签，不能靠大量空结果提高分数。若真实数据无法满足某项配额，需在任何模型调用前记录原因及方案版本；不得执行后换题或减分母。额外合成/变异数据库只作诊断，单列，不冒充官方原数据成绩。

## 连续五轮协议

四段会话均有不同固定 session id；同一段按原顺序运行，系统保存实际回答及实际失败状态，不能用 reference 答案补历史或用 gold 修正模型计划。

| 轮次 | 预先规定的交互职责 |
|---|---|
| T1 | 明确主题、指标、时间和范围的完整新问题 |
| T2 | 同主题维度或指标追问，继承正确时间/范围但不偷加条件 |
| T3 | 加入明确排除、缺失值、复杂聚合或嵌套约束；其中至少一段含当前可能不支持的可解查询 |
| T4 | 明确切换到另一业务主题；原主题指标、维度、日期角色和过滤不能泄漏 |
| T5 | 对新主题追问，或完整重述后返回旧主题；按冻题明确规定应继承/清除的槽位 |

四段应分别检验同主题继承、日期角色替换、失败后主题切换、跨主题再返回。主题切换在同一个冻结 SQLite 中不同业务表之间发生；本方案不把切换数据库连接当成已实现能力。可在一段 T3/T4 之间做预先冻结的会话存储恢复检查，需保留真实历史并单独记录恢复路径。

T3 若失败，仍运行 T4/T5，检验失败没有污染后续状态；整段全通过仍为失败。只有人工提前设计的实际信息不足题才能单列“澄清质量”，不计入可解问数主层；不能把不支持的正确可解查询改成 expected clarification。

## reference 与系统输入隔离

1. 独立作者在冻结的真实 SQLite 上编写 SELECT/WITH reference SQL，不使用被测 planner/compiler，也不使用系统生成 SQL当 reference。
2. reference 使用独立 `mode=ro`、`PRAGMA query_only=ON` 评分连接，禁止写操作和扩展载入；执行全部结果，固定字段角色、类型、排序、NULL 和数值容差。
3. 先保存 reference SQL SHA、完整期望结果的规范编码 SHA、行数、字段数和 source/schema SHA；审阅并冻结，再允许第一次模型调用。
4. 系统进程仅接收题目、真实 Schema、允许的数据取值剖析、reference_date 和真实 session 历史；reference SQL、期望值、评分映射、未来轮题目不能进 provider payload、aliases、metric catalog 或知识库。
5. 父代理/实施代理仅接收 case id、标签、数量、指纹和冻结状态；未见问句、reference SQL 与答案由独立评分工件保存，不在进度或预览中打印。

本草稿没有未见题/reference 工件，`baseline_ready=false`。下一阶段需独立作者与审阅者完成参考结果检查后再生成不可覆盖的冻结 manifest。

## 严格判分与留痕

- 可解题只有 `status=ok` 且全部请求字段、完整结果、NULL、重复行、必要排序和来源约束正确才通过；error、unsupported、clarification、incomplete、截断均失败。
- 对无序问题比较完整多重集，保留重复行；对排序/排名问题比较规定顺序与并列规则，不能只排序后比较集合。
- 整数和 NULL 精确比较；NULL 不等于 0 或空串。源 money/decimal 转 SQLite NUMERIC 的容差须在执行前按来源精度固定并披露，不用宽容差掩盖聚合错误。
- 完整请求不得通过删除生成 SQL 的 LIMIT、提高服务行上限、补 reference 行或分页拼接来救分。显式 Top-N 按用户请求范围完整评分；无用户 LIMIT 时 `limit_reached_total_unknown` 保留失败。
- physical projection 由预冻结的输出角色及生成 SQL/计划的真实来源验证，不按答案数值猜列，也不使用每题展示标签补丁。不同合法 SQL 可通过，不要求与 reference 文本一致。
- 每段五轮以五轮全部通过计通过；同时报告逐轮、业务范围、难度主类、空结果、缺失值及截断数量，不能用简单 COUNT/SUM 的平均分遮盖嵌套/全量失败。
- 每次运行记录全部计划题量、实际执行、not_run、API 审计、来源/Schema/题目/评分代码/实施代码起止 SHA。401/403 等中止保留未运行题和固定分母；不得静默丢题。
- 第一次执行后本题集即转开发；所有失败原样保留。后续修复需开发回归与新来源/新题首次评测分栏，不能混拼为同一次未知准确率。

候选工具职责和 JSON 契约见 `TOOLING_DRAFT.md`；来源与曝光事实见 `SOURCE_MANIFEST_DRAFT.json`。本方案不修改或调用现有评测工具。
