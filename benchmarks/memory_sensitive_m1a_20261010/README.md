# M1-A exposed memory-sensitive development v1

16 个自建、公开可审查的合成开发任务，真实执行 OmniAgent/SQLite/原始 txt+xlsx；不是真实企业私有数据、官方成绩或盲测。2026-10-10 执行前冻结 tasks/gold/sources/candidate_pool、evaluator/scorer、已有 fixture helper/seed，共 8 个文件。所有 SHA 在 manifest.json；manifest SHA256 `0b81fde89bd91604e4c5e970c35af7d9f5560723436506bc17c275f8a2c1c4e6`。

- 输入：tasks.json，仅原始问题、session/preparation 标识、scope、来源版本场景、候选 ID，没有最终数值或 gold SQL。
- 评分：gold.json 与 tools/score_memory_sensitive_m1a.py；仅评分侧读 gold。SQL 复用冻结旧工具的结果/字段/输入记录集合检查；跨源独立计算基准 SQL×当前参数，校验文档 SHA、参数行、定位和必要工具图。
- 来源：sources.json 原始术语确认卡、目标公式与区域参数。数据库由已有固定 build_fixtures+seed 建立，96 条商业样本 + 3 条借贷冲突记录，target fixture=mixed。
- 经验：candidate_pool.json 只含术语绑定/方法/作用域/有效期/来源和反例，不含最终答案。确认是服务器编写的**开发 fixture 合同**，不冒充已认证真实用户。method 在 preparation.json 中有真 DependencyAgent 工具运行及独立评分证据；未注入目标。
- 数据和运行分离：数据库/原始资产在被忽略的 runtime/；公开逐题 JSON 和 SHA 在 docs/memory_rl/runs/。没有 QiMem 私人数据。

| 类别 | ID | 目标与独立期望 |
|---|---|---|
| 跨会话语义/口径 | s01–s04 | 前置明确问题在 preparation session；新 session 查蓝星成本/清泉线上口径/紫竹毛利；年份/地区变换，不复用旧筛选或数值 |
| 跨源方法 | f01 | 当前公式+当前 Excel 参数+新查 SQL；memory 只能建议证据顺序 |
| 方法遇到来源更新 | f02 | 当前华南参数 .18，不能沿用旧 .10；原 method dependency 已变化，必须拒用旧绑定或重新取得可验证证据 |
| 过期/变更/冲突/隔离/未验证 | g01–g05 | 只允许安全拒答/澄清，无 SQL/结果行；A 只能证明输出拒用，不能证明尚不存在的 memory filter |
| 无关题 | u01–u02 | 询问销量/全年销售额，不继承线上口径或成本 |
| 标准、同会话、新会话控制 | c01/h01/h02 | 标准成本可计算；同会话“那华南呢”保留指标；新会话同问须缺少上下文澄清 |

B/C 将共享同一输入/评分和候选池：`cost/margin/online` 当前来源+fixture confirmation+Schema 存在时可用；`method` 只有 prep 独立通过且依赖当前时可用；`expired/changed/foreign/unverified` 不合法；`conflict-sales/conflict-cost` 未解决冲突，不允许选一个当真值。Oracle 也必须遵守这些条件。f02 的旧 method 不能被 Oracle 选入；它属于来源更新完整性压力题，不能预设记忆必定提升该题。B/C 不传 gold，最多 3 条/1800 字，与 A 同模型/工具限额；是否采用此预算待后续审核，调整需新版本任务配置与说明。

当前仅 A，10/16；术语 0/4，方法/更新 0/2，控制题 10/10。4 个术语题实际 document=ok，但返回定义而非所需 SQL 数值，判错；两个跨源题 rules-only 澄清。B/C = not_run。source-change/conflict/scope_guard 的 A 通过只是一条空记忆控制，**不是治理能力已实现的证明**。真实模型基线 not_run（未使用获批 endpoint/付费预算）。

复跑时用新 label 保留已有证据：

```bash
uv --cache-dir /tmp/omni-m1a-uv-cache venv /tmp/omni-m1a-replay --python /usr/bin/python3
uv --cache-dir /tmp/omni-m1a-uv-cache pip install --python /tmp/omni-m1a-replay/bin/python -r docs/memory_rl/runs/requirements-initial.txt
/tmp/omni-m1a-replay/bin/python tools/evaluate_memory_sensitive_m1a.py --label m1a-no-memory-replay
/tmp/omni-m1a-replay/bin/python tools/verify_memory_sensitive_m1a_scoring.py --label m1a-no-memory-replay
```

`--mode B/C` 当前明确报未实现并退出，不产生伪成绩。源码/数据前后 SHA、包版本、Python/SQLite、engine 限额、0 模型调用/token、trace 计数和逐题 wall time 在 summary.json。trace running 次数不是低层 SQLite 调用计数；没有性能优越性/公网延迟声明。未来记忆目标 session 必须仍为空，不得把 preparation history 注入新 session。
