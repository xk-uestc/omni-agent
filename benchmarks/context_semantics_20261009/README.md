# 上下文与口语语义升级回归（2026-10-09）

这是本项目自建的开发回归集，不是官方赛题成绩，也不是独立盲测。输入在候选实现完成前冻结，参考 SQL 只供评分器使用，不传入规划器。所有调用经过真实 `OmniAgent.query → Nl2SqlEngine → SQLite`，关闭外部模型，不调用付费 API。

## 固定输入与数据

- `cases.json`：185 个有独立预期的单轮/多轮回合。难度从标准单指标逐步增加至口语、多轮保留/修改、跨域切换、字段歧义、安全和匿名 schema。
- 旧能力 31 回合；跨领域口语 25；销售成本/利润/销量/收入口语 36；匿名 schema 12；多轮 52；话题切换 8；本金歧义 7；缺失与安全 10；重置 3；字面量保护 1。
- 丰富 demo 数据来自项目已有的可复现合成 seed；商业、金融冲突、匿名字段和无财务语义的数据库由评测脚本自建。自建商业数据含 2023–2026 年、3 个地区、4 个月份、2 个渠道共 96 条数据；借贷冲突数据另含 3 条记录。
- 匿名 schema 配置只声明标准业务含义，例如 `expense_r73 → 成本`，没有加入评测口语。字段名本身不被假定能提供业务语义。
- `demo_metric_catalog-frozen.json`：独立复制的基线业务指标定义，避免运行中外部配置变化。
- 输入 SHA256：`9fbeca112d7ae5d3280e681c26f023c16b42cc3af9ad963551f05aecd3269afa`。

## 验证方法

需要给出答案的回合，同时检查状态、真实指标字段、维度、所有结果单元格、只读重放 SQL 和独立 gold SQL 的输出。另对两个 SQL 的实际输入记录集合比较，防止偶然相同的合计掩盖遗漏时间、地区、渠道等筛选条件或使用错误数据表。

多指标编译器可以使用每指标独立聚合 CTE。评分器逐个验证所有物理来源 CTE 的输入记录集合；聚合后单行交叉合并不会被误判为原始记录扇出。`candidate-2-audited.json` 是保留原始生产回答和计时的重评分记录：修正合法 CTE 的评分假阴性，不改任何 gold，也不重跑问答。故意让某个 CTE 改成错误年份的反例已验证会被拒绝。

需要澄清的回合严格检查 `clarification` 或结构化安全拒绝。旧系统的 `insufficient_evidence` 保留原状态并记为不满足澄清契约；安全问题即使执行了无害的只读子问题，也不能作为完整安全拒绝通过。数据库前后 SHA256 必须相同。

连续会话逐回合执行，失败结果保留，不重建预期成功状态。这样可以暴露上一回合失败给后续追问造成的影响。完整 12 回合长会话覆盖项目原有 8 回合窗口后的继续问答。

所有数据库/源码前后散列和逐回合 SQL、参数、结果、澄清状态、上下文解析和墙钟耗时都保存在 JSON。不保存任何凭证。

## 复跑

```powershell
python tools/evaluate_context_semantics_20261009.py --label candidate --compare-to benchmarks/context_semantics_20261009/baseline-audit.json --repeats 10
```

正确性与性能可以分开，避免其他回归任务的 CPU 负载影响性能：

```powershell
python tools/evaluate_context_semantics_20261009.py --label candidate-correctness --compare-to benchmarks/context_semantics_20261009/baseline-audit.json --skip-latency
python tools/evaluate_context_semantics_20261009.py --label candidate-performance --compare-to benchmarks/context_semantics_20261009/baseline-audit.json --latency-only --repeats 10
```

正确性循环结束会先写运行目录的 `correctness-checkpoint.json`。只跑性能的报告明确 `run_mode=latency_only`，不声称重复验证了全部 185 回合。最终性能只比较两版本每次重复都答对的相同题号。

固定旧实现快照位于 `runtime/context-semantics-20261009-baseline/backend`，基线复跑可指定：

```powershell
python tools/evaluate_context_semantics_20261009.py --label baseline-replay --implementation-root runtime/context-semantics-20261009-baseline --repeats 10
```

每个 label 创建独立运行目录，现有报告不覆盖。原有 `baseline.json` 是首次输出比对记录；`baseline-audit.json` 在相同输入和相同旧源码下，进一步增加源表输入记录集合的验证，作为候选比较基线。

## 延迟比较边界

固定 12 道丰富 schema 的标准问题，每题 10 次串行重复。先预热 schema 和每题；计时覆盖完整 agent 调用，评分器不计入延迟。错误/澄清答案不作为正确答案的速度收益。候选比较只使用两版本都正确的相同题号，另外列出新增成功和倒退题号。

这是本机本地规则链路耗时，不能证明公网、外部 API、多模态、RAG 或并发场景的延迟没有退化。运行时其他进程负载仍可能影响毫秒级波动；报告保留每次原始观测，不把一次 p95 差异表述为未来性能保证。

## 建议与本次集成一起运行的旧能力回归

`test_rich_demo_followup.py`、`test_sql_history_scope.py`、`test_sql_history_scope_integration.py`、`test_executed_scope_edit.py`、`test_clarification_continuation.py`、`test_clarification_endpoint.py`、`test_history_reference.py`、`test_ordinal_rank_selection.py`、`test_stable_query_references.py`、`test_round7_generalization.py`、`test_round7_whole_question.py`、`test_schema_profile.py`、`test_schema_alias_roles_round6.py`、`test_value_index.py`、`test_model_sql_followup.py`、`test_nl2sql_baseline.py`、`test_complex_query.py`、`test_metric_compiler.py`、`test_api_security.py`。

前端继续回归会话 session、上下文、来源单元格高亮。独立公共泛化集和真实模型链路仍应另列，不以本自建集替代。

## 补充独立边界审计

这些探针在固定 185 回合之外单独统计，不替换冻结输入或 gold。

- `tools/audit_context_boundaries_20261009.py`：21 道定义/金额/最高排名/工资/网页浏览量边界题。`routing-audit-confirmed.json` 为 21/21，源码和数据库前后散列稳定。定义题核验资料路由、不改写为其他术语、不执行数值 SQL；资料不足保留 `insufficient_evidence`，不将其宣称为检索成功。统计题使用独立参考 SQL 核验值、物理字段、维度和来源记录集合。
- 合法 `DENSE_RANK` 包装查询的输出和 SQL 重放在首次补充评分中已经正确；补充评分器新增受限的排名子查询来源核验，不改变任何问题或预期。初次审计报告保留，用于追踪评分假阴性与真实修复。
- `tools/audit_semantic_clarification_20261009.py`：22 回合待澄清语义作用域。覆盖成本/借款本金选择、之前成功查询的干扰、新主题或完整新问题取消旧待澄清、重置、会话隔离。数据源使用已经冻结的混合商业/借贷夹具，选择“成本”必须保留原始 2025 年华东条件，返回 2375.96；选择“借款本金”必须返回 12345。
- 原补充 22 回合的探索报告 `clarification-audit-final.json` 保留原问题和 gold。它对已有销售历史之后的“本金”要求必定澄清，这一政策过严：销售上下文本身可以提供成本语义。因此新建 `clarification-cases-explicit-new-topic.json`，仅在已有历史后的两个歧义问题中明确“换个主题”，验证独立问题不能沿用旧领域。新版输入 SHA256 为 `684e8d955920f9b793a7a5069e90c9837a098296bea0743d0344bffb0afc434f`，不能与原探索 22 回合冒称同规格改善，也不影响固定 185 回合。
- `input-gold-audit.json`：独立以 SQLite 只读连接重放两个冻结输入中共 188 条需要答案的参考 SQL，0 错误，5 个数据库前后散列不变。参考 SQL 不进入规划流程。

复跑时使用新的 label 保留历史证据：

```powershell
python tools/audit_context_boundaries_20261009.py --label boundary-replay
python tools/audit_semantic_clarification_20261009.py --label clarification-replay --policy explicit-new-topic
```
