# F1 架构审计（2026-10-10）

审计起点为 memory 94a9926de2cee14c014a9c6e93c63112407d7760；fetch 后远程无后续终版。保留工作区已有报告并归档为 16c5d28729acfb0ebba7b8930426b2a3241bf427。本阶段不改 M1–M4 战略，不调用远程模型。源码逐文件 SHA、环境与原始日志见 runs/baseline-manifest.json。历史数字只作为失败线索，不能代表当前 HEAD。

下表路径均相对 ict-track8/backend。DocumentHit.document_id 实为 chunk ID，真实来源 ID 位于 metadata.document_id；混淆二者会错误绑定来源。

| 环节 / 核心入口 | 输入 → 输出 | 依赖 / 已有验证 | 当前问题与修改判断 |
|---|---|---|---|
| HTTP/SSE：app.omni_query / omni_query_stream | OmniRequest → JSON / SSE events | OmniAgent、ConversationStore；memory_api/task_experience_api/omni_query_stream | 统一拒绝应发生在改写、记忆、规划之前；SSE 超时并不等于 worker 被取消 |
| 意图与规划：omni_agent.query / _query_without_memory / _query_turn / _normalize_model_tasks | 原问题、历史、schema、catalogue → route、有效问题、任务 DAG | 语义图、typed model schema、completion_errors；omni_agent/semantic_integration/plan_recovery | safe-09 危险片段被剔除后执行剩余查询；模型协议失败与未完成需统一分类 |
| 来源定位：omni_agent.catalogue / search_scope | 问题、资料列表 → 来源候选 | KnowledgeStore、source_constraints；fusion_source_constraints/sql_document_binding | 页层和精确标识已有能力，但没有全局两级来源候选检索；标题不是事实 |
| 持久化：knowledge_store.ingest / records / document / verify_source | 原字节 → SQLite document/chunk payload、SHA；chunk → 可信原件 | chunk_cleaning、DocumentAnalyzer、SQLite；knowledge_store/source_revision | 每次 search 全库反序列化，指定来源也不下推 SQL；先测量再下推。内容寻址旧资产保留，逻辑替换在事务内 |
| 解析：chunk_cleaning.DocumentChunker.parse_pdf/docx/xlsx/image、text_structure.chunk_text | 原件 → DocumentChunk(页/行/表/locator/quality/warnings) | fitz、pypdf、openpyxl、docx、OCR；大量 native/OCR/outline 测试 | 清洗、跨页表头、扫描件仍有不确定性；不能仅凭 bbox 推断关系，不整体重写解析器 |
| 检索：knowledge_store.search、cross_source.JsonDocumentRetriever.search | 问题、top_k、来源/页范围 → DocumentHit + retrieval_audit | BM25、正文 IDF 覆盖、EvidenceCoverage；knowledge_store/evidence_coverage | 重复构建全词频；源片段多会挤占候选。需冻结 A/C 对照，无增益则不开默认开关 |
| 向量：dense_retrieval.LocalBgeEmbedder.embed / DenseIndex.vectors/search | 当前文本+模型 identity+NORMALIZATION_ID → 向量、cosine、RRF | 本地 BGE、SQLite；dense_retrieval | 每次读取所有历史向量；修改为只读所需 key。中文 encoder 对英语实际跳过，health 名称不是执行证据 |
| 证据选择：evidence_coverage.select_coverage_hits | 真实正文、ranking、预算 → 候选及 lexical audit | 去重/来源多样性；evidence_coverage | 明确 lexical coverage≠语义充分；不将候选耗尽作为完整来源闭集 |
| 补证：evidence_recovery.recovery_eligible / plan_evidence_recovery | 未完成结果+本次 completed audits → 至多3查询、1轮 | ResponsesClient、原文 context；evidence_recovery_round5 | 已有 provider/transport 守卫，保留；本轮不扩大模型调用预算 |
| 原页：source_answer_dossier.build_dossier / route | 当前 SHA 原 PDF、检索页 → 完整页+ledger、独立 review 与 replay | fitz、native_page、offset proof；source_answer_dossier 测试、历史 Round38 | 最多4来源/128扫描页/6证据页/32000字；long doc 仅邻页，完整页≠完整文档，需结构化 scope |
| 多片段：source_multi_span_answer.bind_multi_source_answer / replay_multi_source_proof | question contract+原文引文 → 各子问 literal fragments+inventory | source_span、typed review；source_multi_span 测试 | 完整性只对 supplied evidence 成立；重复同名实体/排除条件不能从片段关联猜测 |
| 原表：pdf_page_index.complete_pdf_page_index、native_table_chain、native_row_selection.bind_selection | 当前 PDF SHA → 页索引、表链、typed predicates、完整匹配行 | 原生布局/表头/独立 review；native_row_selection/native_table 测试 | 多行枚举与跨页是重点；沿用已有 typed compiler，不新增 Gold 特判 |
| 跨源执行：dependency_agent.validate / run / _run_ordered / execute | DAG、原问题 → results/trace/source_validation/skipped | SQL只读快照、formula/cell、动态来源绑定；dependency_agent/fusion/source tests | C/D source binding 拒绝保留；上游工具 status 非 ok 可能仍标 complete，必须辨别机械执行与整题完成 |
| SQL验证：nl2sql.engine.answer、security.execute_read_only | 原问题/typed plan → SQL、rows、provenance | SQL AST、authorizer、预算、独立结果重放 | safe-09 统一拒绝；显示 alias 与物理 SUM 核验现有失败需诊断，不能降级安全证明 |
| 答案与展示：knowledge_store.answer / _answer、source_answer_dossier、answer_contract | 来源证据+原完整问题 → answer/status/citations/proof | 独立 review、SHA/locator replay；Round38历史15/36 | 真实模型本轮未跑则 accuracy/fidelity=not_run；模型自评不替代独立整题 scorer |
| Memory：memory.adapter.run/observe、experience.ExperienceSelector、formation | 决策状态、候选、动作、真实工具反馈 → events/candidate | scope/schema/source版本/审核/撤销；16/185/22题及机制测试 | 已有仅1条有效经验，固定收益未成立；本轮改善基础设施不归因为记忆收益 |

只进行能保留入口契约的局部改进。实际完整系统仍是原 OmniAgent，不引入另一个 RAG 主架构。
