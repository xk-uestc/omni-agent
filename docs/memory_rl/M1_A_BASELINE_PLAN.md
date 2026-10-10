# M1-A 基线准备（开发集）

## 已有工具与版本

`tools/evaluate_context_semantics_20261009.py`：固定 185 回合，真 OmniAgent/SQLite，gold SQL 与物理来源输入记录集合对照，数据库/源码前后 SHA，不用付费 API。输入 `cases.json` SHA256 `9fbeca112d7ae5d3280e681c26f023c16b42cc3af9ad963551f05aecd3269afa`；历史 `candidate-final.json` 185/185。`demo_metric_catalog-frozen.json` 独立目录，build_fixtures 使用合成 seed + 自建混合/商业/匿名库。冻结文件和评分工具不修改；新 label/new output 保留历史。

相关 HTTP 工具 `tools/verify_context_semantics_http.py` 依赖可运行后端服务；它验证传输/服务实际配置，不作为新术语记忆的独立主评分。需另行记录 HTTP/SSE 本地入口回归，不宣称生产公网已测。

## 新开发任务冻结要求

将在 `benchmarks/memory_sensitive_m1a_20261010/` 保存开发输入、合成来源/候选、评分 gold 和 SHA 清单；在 `tools/` 保存 evaluator 与独立 scorer。不使用/改动正式冻结测试。候选只是后续 B/C 的合法经验素材，不是产品 Memory Adapter。全部为已曝光自建合成业务任务，不是官方/公开基准。

任务包含：术语确认前置查询后另开新 session 的复用；明确渠道/指标口径；已有同会话追问控制与新会话缺少上下文控制；真实 SQL+原始文档公式跨源方法；来源更新/过期/冲突/foreign scope；无关问题不得引入约束。前置任务没有目标题最终答案，gold 仅在评分侧。目标题用完整年月/地区，避免把恢复同会话筛选误算为跨会话知识收益。

优先本地规则模式，不将未配置真实模型的跨源端到端记忆任务写成已运行。每题保存 response/trace/SQL/parameters/provenance、耗时、原件 SHA、数据库版本；执行发生与正确性分开。独立评分用只读数据库参考查询、来源输入记录集合/字段/引用检查，不依赖 agent 自评。

## A/B/C 同一协议

- A No Memory：无 adapter、无候选注入，原系统原样；允许同会话控制历史，跨会话目标使用空历史。
- B Fixed Memory：后续获批 adapter 后，固定算法、合法候选、预算和有效性过滤。
- C Oracle Memory：只能从同一个合法且当前有效候选池理想选择；无 gold SQL、最终数值、目标答案、额外数据库工具或不合法候选。空池应拒用。
- 相同输入/评分/数据初始状态/reference_date/模型/工具预算，候选有效性规则与 scope 一致；冲突/变更拒用单独报告，收益题与安全控制题分母分开。
- 同会话与跨会话有明确 ID、fresh target history 检查；不以同会话自然优势冒充记忆收益。
- B/C 目前 `not_run`：Memory Adapter 尚未实现；不产生对照成绩。

计量包括任务成功、来源覆盖、误用、澄清、模型调用/token（无模型为 0）、工具 trace 次数、wall time。模型配置如无 provider 必须标明 rules-only；工具 trace 不是低层 SQLite execute 次数。异常和未运行保留真实原因。正式 HTTP/SSE 可信 scope、跨用户隔离、重启记忆尚未实现，不作为通过项。

## 已冻结并实际执行的 v1（2026-10-10）

资产与复跑：`benchmarks/memory_sensitive_m1a_20261010/README.md`。16 个 task ID；前置数据与 gold 严格分离，库/原件都从公开合成 fixture 生成。`manifest.json` 在执行前记录 8 文件 SHA256；SHA 为 `0b81fde89bd91604e4c5e970c35af7d9f5560723436506bc17c275f8a2c1c4e6`。任务输入 SHA `bdd733464b254d5239983709d5a9fc8de11141ed6e47e719e490ec99a628f725`，scorer SHA `3e2c8558ad6ac5acf1f71d528dbcc21f9e425fcbe4f0d6a6c5a2251f69b46cbc`。执行前冻结与执行中 hash 一致。

A 的六项收益/完整性要求 0/6，其中 f02 为来源更新压力题：旧经验依赖已经不合法，不预设合法 Oracle 可提高它。其余 10 个控制任务满足响应契约，包括 h01 有 1 轮同会话历史、其余 target 0 轮历史。s01–s04 使用原知识库检索可取得术语定义，但没有计算所问值；这是语义接入与资料问答的区别。

当前 DB SHA `bf0fb0e1308062b8840810bf0ce26d1c8efff67e61c127576718f410ae3fe6e2`；aliases SHA `cf7601df26007f56b169390f225f8e337c532e6bbc9bd06c11cb810ca63d3dcb`；原件所有版本在运行 summary.json。原目标 Excel SHA `b58186f6894e0dfd730e1d9e9242f5b2ce146a7719801f2e64aebb85b79cf4d1`，新版本 `aec45342585faaea195011bf40f5e6a77cc3888b1d7e8e2bdfae355ac9f0eea8`。XLSX ZIP 时间归一化，避免重跑时仅 ZIP 元数据改变。

配置：Python 3.12.3 / SQLite（summary 中精确版本），rules_basic、无 client/model_plan_provider/generator/embedder、FAST_SQL=0、reference_date=2026-10-09；候选时态 reference_time=2026-10-10T12:00:00+08:00；max_rows=100、max_steps=50000000、max_seconds=5。生成固定无随机采样，无外部 API、token=0。初次 34 个包完整 lock 在 runs/requirements-initial.txt。后续 B/C 使用相同锁与预算，新增 provider 会改变实验条件，需单独版本而非混称本次结果。

独立评分验证最终 6/6：正确前置方法通过，错误终值、文档 SHA、参数行、SQL 输入集合、缺失公式证据均拒绝。最初反例脚本试图替换参数化 SQL 中不存在的年份，实际没有改变 SQL，得到 5/6；保留 scorer-negative-checks.json，修正测试为改变 parameters 后为 6/6。没有修改冻结 scorer/gold 来补分。

原 185 回合实际 177/185（不是本轮 185/185），独立探针与原因见 runs/context-diagnostic.json 及 CODEX_REPORT；数据库和源码不变。No Memory 的检索误用率/跨用户记忆隔离 **not_measurable_without_adapter**，控制题通过不能据此声明治理系统通过。
