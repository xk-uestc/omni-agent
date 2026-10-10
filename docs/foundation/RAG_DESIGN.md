# F1 分层评测与检索设计

冻结自建开发24题、保留8题；34来源（原生PDF含长文档/跨页表格、中文语义问、同名实体、多文档、单位/日期/排除、标题干扰、24密集噪声来源），实际数量以documents.json为准。输入在 benchmarks/foundation_f1_20261010，源码/输入/评分器 SHA 见manifest和每次报告。数据、评分、运行产物分开；非官方成绩、非外部盲测，未读取正式隔离Gold。保留成绩不参与策略或参数选择，工程冻结后做同源前后比较。

A：现有真实 KnowledgeStore BM25，含既有页/正文重排与coverage selector。
B：相同输入/预算的真实本地 bge-small-zh-v1.5 + BM25/RRF，权重遵循现有manifest版本，不换encoder。逐题记录dense_status；纯英文因模型只支持中文而跳过，不能把整组叫作每题执行Dense。
C：最小文档层导航，最多6来源，原chunk内召回池20、top_k4。导航只影响候选范围，证据仍来自原KnowledgeStore。来源标题不构成事实证明；原标识/SHA/locator核验保留。C须有可解释净增益才考虑默认开启，否则仅显式实验策略。

评分层：source recall@4、必要literal evidence recall@4、binary qrels MRR/NDCG、无关来源比例、SHA/locator一致性、annotated record coverage、错误complete声明、查询P50/P95、RSS、入库/向量预热耗时。完整引用/端到端答案正确性、忠实度、错误或合理拒答、模型Token均标记not_run（未授权付费生成）；离线embedding不是答案模型。合成相关性标签仅在检索结束后评分，不进入query/context/索引。

证据范围状态设计：complete_for_declared_scope / incomplete / ambiguous / source_changed / budget_exhausted。声明范围是具体来源版本与页/表/行/必要条件；扫描过与供给答案器的证据分别计数。完整原页、词项命中与耗尽召回候选都不等于闭集完整。多行枚举与跨页/多文档范围是优先工程对象，依据Round11/38失败归因而非少数OHR Gold。

存储只改有证据的路径：单文档查询 SQL 下推，Dense 按缓存key读取与校验，不增加旧答案缓存、不跳过SHA。外部更新、删除、重启、并发和损坏缓存要在独立临时库验证。保持旧数据库兼容，不实施破坏性迁移。
