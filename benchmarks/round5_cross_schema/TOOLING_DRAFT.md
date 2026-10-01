# 独立工具候选草稿

本文件只定义后续工具职责和输入契约，不包含未见题、SQL 金标、期望答案或可调用 API 的脚本。现有 `tools/evaluate_adventureworks.py`、`tools/evaluate_new_multiturn.py` 和 `tools/prototypes` 均不修改。

## 工件与进程边界

| 新工具候选 | 输入 | 输出 | 禁止事项 |
|---|---|---|---|
| source_port | 已固定官方资产、转换契约 | 新目录中的 SQLite、来源 manifest、类型/键/NULL 审计 | 覆盖现有官方库；声称原生 SQL Server/PostgreSQL 适配 |
| reference_freeze | 只读真实 SQLite、独立作者题目和 SELECT/WITH | 评分专用 reference、完整期望行、预冻结列角色及 SHA | 导入被测 planner/compiler；运行模型挑简单题 |
| system_input_freeze | 经审阅的题目、实际 Schema、日期、会话顺序 | 仅系统可见包 | gold SQL/行/评分映射、未来轮问句进入 provider payload |
| cross_schema_runner | 系统可见包、固定实现和 API 配置 | 原始系统响应、逐次 API 审计、模型输入摘要及来源/源码起止 SHA | reference 进入系统进程；改 LIMIT 救分；失败后改题 |
| reference_score | 原始响应、评分包、相同只读 SQLite | 全量结果比较、错误类别、业务/难度/五轮分层报告 | 用答案值猜列；抹去重复行或排名顺序；澄清算答案通过 |

系统进程和评分进程分别加载两包；runner 不导入 reference 模块，不把评分包路径传入系统对象。评分包可保存于独立评测目录，父/实施代理只获得公共 manifest、case id、难度数量、指纹与状态。原始响应中不得嵌入 reference。离线 preflight 默认只输出数量、hash 和错误代码；无包含题目/答案的 preview 模式。

## 冻结契约字段

公共 manifest 至少固定 `source_commit/release`、原资产/SQLite/schema SHA、许可证、scope/exposure_label、转换版本、独立题与会话数量、难度配额、题目包 SHA、评分包 SHA、reference_date、规则/模型模式、模型标识和 reasoning、评估工具 SHA、backend 文件 SHA、每轮预算、结果比较策略及空结果比例。

系统可见 case 只包括 `case_id`、`question`、`database_id`、`reference_date`；会话另有 `session_id`、`turn_index`。runner 对同一 session 逐轮发送当前 question，历史只来自实际系统存储。难度标签和预期状态都不传系统。

评分专用 case 固定 `case_id`、`reference_sql`、`reference_parameters`、`expected_output_roles`、`expected_types`、`full_expected_rows`、`comparison_mode`（bag/ordered）、`tie_policy`、`numeric_tolerance`、`nullable_semantics`、`requires_complete_unbounded_result`、`expected_history_invariants` 和 `answerable=true`。不得为当前不支持的可解查询把 `answerable` 改成 false。

## 离线 reference preflight

- 使用 `mode=ro` URI、`query_only`、只读 authorizer、有限步数与墙钟；SQL 仅单个 SELECT/WITH，禁止写、ATTACH、扩展载入和运行配置修改。
- 对固定 source/schema SHA 执行独立 reference 两次，比较完整规范编码 SHA；非确定排序需改为预定义 bag 比较，不能依赖 SQLite 偶然行序。
- 两位独立作者/审阅者核查 JOIN 粒度、去重实体、NULL、日期角色、日期范围、条件聚合、最新记录、反连接和排名并列。可用第二条语义等价 reference 交叉核对；不能让模型系统作答来确认 gold。
- 真实结果大于 100 行的案例在冻结前确认行数，但不打印行值。全 NULL/空结果要保留类型与结果形状，例如单行 NULL 聚合不能等同零行结果。
- 题意歧义在首次执行前由独立审阅者修正或按预声明准则剔除并补足同难度；冻结后不得以不支持或失败为由剔除。合法安全/信息不足题另建非主分母诊断，不混入可解集。

## 独立评分

先验证响应 `status`、生成 SQL、参数、输出字段角色、真实来源和实际执行回放。允许不同合法 SQL；不要求文本一致。回放保留生成 SQL 自带 LIMIT、排序和过滤，不能删除 LIMIT 或用 reference 补全。实际返回与回放结果也要一致，不能只评分系统未返回的更多数据。

完整性首先比较全部请求行及列，再对无用户限制的查询要求来源 `result_completeness` 未声明未知/截断；超过产品上限仍留为失败。显式 Top-N 只按请求范围比较，但必须包含该范围全部并列结果。按题目要求选择完整 bag 或 ordered 比较，不统一 sort 后掩盖排序错误。

复杂投影不能沿用只支持“若干维度加最后一个指标”的旧 projection。输出角色要在评分包中事先定义，通过返回计划/SQL 物理来源与列顺序核查，而不是给模型提供 expected 标签。整数精确；NULL 精确；类型不随便归一化；decimal 容差只在来源转换说明允许时使用。

逐 turn 输出 pass/failure_reason、status、result_state、返回/期望行数、完整性状态、错误/clarification code、API 使用和 hash，父代理进度不显示未见 question/SQL/rows。会话 all_pass 是五轮结果的逻辑 AND。unsupported、clarification、partial、error 永远不能满足可解答案。

认证失败、资源限制或外部中断可终止模型调用，但报告保留 planned_total、executed_total、not_run，不缩减分母或将未运行算成功。正常题目失败不打断后续轮次；特别要继续主题切换，核查失败状态是否污染历史。

## 必须先检验的评分器边界

后续实现评分器时，用独立小 SQLite 验证重复行 bag、NULL/0/空串区分、单行 NULL 与零行、整数/decimal 容差、全部字段、顺序/并列、LIMIT 截断、当前题不可解替代成澄清、API 中断分母、失败历史及金标隔离。此类测试验证评分合同，不作为模型或跨 Schema 通过率。
