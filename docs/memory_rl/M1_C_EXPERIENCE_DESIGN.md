# M1-C 跨源经验：诊断先于实现

起点：memory `5a5f14f8c5aeab0aa9ce331da8b628bcd98933e0`；main保持`ac69d6e2f7a4a0fe7707e6dee9bab91a52078f7a`。用户授权M1-C，补全附件第9节，仍禁止M2训练/合并main。QiMem原包未动。现阶段先记录客观诊断，后续实现/成绩另附，不改历史结果。

## 检查点1：四种工作流

复用rich demo数据库、原M1-A policy/targets资料、SQL→文档测试协议，增加明确自建的团队方法TXT与修订计划XLSX。非企业数据。完整输入、图、响应、源码Hash/DBHash在runs/m1c-diagnosis-complete-20261010/diagnosis.json。

| 工作流与原需求（完整逐字问题在JSON） | 无模型Omni | 完整原约束下手工图 | 归因 |
|---|---|---|---|
| A 文档公式+2026华东增长率+数据库2025销售额→目标 | clarification | ok，4步完成 | Planner Failure；执行器可完成该表达 |
| B 数据库2025销售额排名第一地区→检索该地区团队方法 | clarification | ok，SQL→search完成 | Planner Failure；证据导航不等同完整自然语言答案评分 |
| C Excel按2026/.12选择地区→SQL过滤查2025销售额 | clarification | incomplete，source_dynamic_binding_unverified | Planner缺失且来源动态绑定拒绝；不能只靠经验解决 |
| D 两份计划增长率比较+数据库销售额 | clarification | clarification，source_scope_unverified | Planner缺失且整题来源范围绑定拒绝 |

四个图均通过基本DAG/normalize及当前completion_errors检查；这不证明任务完整正确。特别是`plan_requirements.py:8–63`目前主要检查显式政策版本日期比较，并不覆盖任意A–D所有需求，最终Gold必须另检。

同图不传original_question的工具诊断：A/C/D为ok，B为incomplete（缺失SQL实体授权归一化）。原M1-B2 3/3探针也未传original_question，本轮明确补齐这一差别。C/D的原约束失败保留，不跳过校验、不修改安全协议凑成绩。A/B可作为经验机制初始种子，必须另外经独立整题评分，不能仅凭ok晋升。

## 真实接入位置

- `omni_agent.py:361 basic_plan`：无模型跨源要求澄清；`_query_turn:1099–1220`：原client.generate→协议解析→completion_errors→_normalize_model_tasks，最多一次修复。
- `_normalize_model_tasks:225`：DAG、SQL检索范围、静态单元格和跨源单路由核验。`DependencyAgent.validate:40`验证工具白名单、引用和环；`run:103`按原问题绑定SQL来源、公式目标、SQL实体检索与动态过滤；`_run_ordered:250`重新核验原件版本并执行。
- `fusion_constraints.bind_source_constraints`、`dynamic_source_binding.bind_dynamic_source_filters`分别是D/C实际拒绝层；`knowledge_store`原件+chunk供公式/表格证据，`DependencyAgent.execute`最终取值与计算。
- 经验应在上述client.generate构建request-local context时附加有界建议；必须仍由同一Planner生成新完整图、原执行器验证。HTTP/SSE都构造OmniAgent，需统一启用开关。不得走继承resolved_tasks捷径。

## 模型条件与待决策

环境未发现ICT8_PLAN/OPENAI等相关变量；仓库根、ict-track8及backend均无.env。仅检查是否存在，不打印密钥。既有`app.py:78–109`的HttpModelPlanProvider是NL2SQL接口；顶层`generation_client`使用ResponsesClient，不能把NL2SQL URL直接当Omni JSON Planner。模型入口、接口兼容及固定预算必须先验证。

用户选择免费本地模型，尚未提供入口/模型/上限，正在询问硬件；建议Qwen3-8B起步或资源充足的14B。官方资料：https://qwen.readthedocs.io/en/stable/deployment/vllm.html 。建议先4题至多8请求（含一次修复），尚未视作预算批准。当前真实模型对照not_run、calls/tokens=0，不称规则失败为经验增益。

后续契约：独立task_experience类型；共享Store候选、验证、审核、撤销和历史，内容只含工具角色/依赖/参数来源，旧参数/SQL/答案留在本地审计轨迹而不进规划提示；从已独立验证的实际执行轨迹形成。研究者合法图种子须标记developer_verified_seed，不称自主学习。非法候选先过滤；确定性选择与Oracle使用同一合法池；来源变更严格拒用。

## 检查点2：类型与形成契约

新增`memory/experience.py`：ExperienceFormation继承原MemoryFormation的候选状态、事务审核、重放幂等、撤销、superseded和历史。MemoryRecord新增可选experience字段；business_semantics继续走原binding逻辑，task_experience.binding必须为空，不作为别名消费。共享`MemoryCore.invalid_context_reason`保留scope/确认/时间/Schema/来源检查，类型独立验证。无SQLite表结构变动；旧JSON无experience字段仍可读取。

capture_verified_run内部API实际调用DependencyAgent.run(original_question=...)；完成后调用可信本地提供的独立整题scorer，四项task_success/operation_coverage/result_correct/source_correct必须全部true。原问题、图、真实结果、scorer SHA与反馈形成不可变source event，审核时检查digest。工具ok而整题错误不能生成候选。开发种子标为developer_verified_seed，不称自动学习。

确定性抽象只保留工具角色、依赖顺序、参数来源类别和重新绑定义务；删除原SQL、年份、地区、增长率及答案。原具体值只留审计事件，不进入模型建议。版本钉住原资料和当前Schema/DB，变更拒用而非默默跨版本迁移。独立scorer是服务器/研究流程的信任边界，不接受HTTP传来的成功布尔值。

CLI沿用`python -m backend.memory.admin --config ... --type task_experience list|validate|review|revoke`。未增公网形成/审批入口。默认共享core仍拒绝task_experience作为业务别名。类型测试实跑11通过；原54项记忆测试全部通过，尚不等于真实Agent经验收益。

## 检查点3：请求级Planner接入

`OmniAgent(...,experience=selector)`只在原client.generate分支建立request-local context后调用选择器；有界建议放在context.task_experience数据字段，原INSTRUCTIONS、用户历史、engine/catalog不变。原Planner必须生成新tasks_json；normalize/完整原问题来源约束继续生效。无模型不执行记忆中的图，mock错误地区图仍被拒绝。HTTP/SSE共享`ICT8_TASK_EXPERIENCE_ENABLED=1`，还要求原MemoryCore可信scope配置；默认关闭。

策略记录候选及拒绝原因、当前来源/工具/任务特征/预算、合法动作、实际选择和执行反馈；audit存入原memory_events，独立整题正确性由评测器另加。在线ok不自动晋升。选取非法ID被拦截；请求局部隔离已测。

新真实模型预检发现B的status=ok掩盖方法证据缺失：选出华北，但诊断资料只有华东/华南，最终答案明确无法回答。这一轨迹不得晋升。后续开发资料显式补充各地区方法，以研究任务依赖而非无资料猜测；缺失来源控制另保留。A预检有当前SQL、公式/单元格及计算答案，最终仍由独立scorer核验。
