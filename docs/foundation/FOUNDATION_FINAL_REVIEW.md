# F1 基础设施最终验收（2026-10-10）

F1 检查点1–7已在 memory 分支完成可执行的工程、实验及证据交付，等待 ChatGPT 审核。没有开始 PPO/SFT/GRPO，没有合并 main，付费模型请求0。结论是执行环境更可审计、部分存储路径更快、整体危险请求拒绝已修复；没有证明新的 RAG 召回收益或 PPO 训练就绪。此验收不是赛题整体达标或公开基准成绩。

最终运行后端源码固定为 `a37c0728deacf1bc01a6e1458ac3826a2d570e2e`；之后的提交只新增测试、实验工具、证据与报告，backend逐文件SHA一致。交付文档自身提交的完整远程SHA见最终反馈（避免自引用提交Hash）。起点为 `94a9926de2cee14c014a9c6e93c63112407d7760`。运行平台是当前Linux/Python3.12，不把历史Windows报告当作当前性能。

## 提交与代码索引

每项已独立提交推送；检查点7是包含本报告与最终资产Hash清单的文档提交。

| 检查点 | 完整提交SHA | 主要交付/代码 |
|---|---|---|
| M1-C收尾 | 16c5d28729acfb0ebba7b8930426b2a3241bf427 | memory_rl终版及历史冻结产物独立审计，未重跑付费实验 |
| 1 调用链/基线 | 98dc39aff5891ed74fda4c14f4ec4a8c4bf64144 | ARCHITECTURE_AUDIT.md、BASELINE_AND_FAILURES.md |
| 2 冻结评测/BGE | 387c5977691a5ea5752111c0fb7158951ca91d2a | benchmarks/foundation_f1_20261010、freeze/evaluate_foundation_rag.py、fetch_foundation_bge.py |
| 3 存储/导航对照 | caddd1f6465bcf0006a66771031a7710f9a31864 | knowledge_store.records/delete、dense_retrieval向量key批读、tools/prototypes来源导航负实验 |
| 4 有界证据 | ec477b649b7a9f6a16deae47e2e7211e18f6596d | evidence_scope.py、source_answer_dossier.py、native_row_selection.py |
| 5 安全独立修复 | dc3284ff04ae7a2fdea4a4506af9378c02863c9b | nl2sql/security/engine、Omni/CrossSource/DAG原请求前置拒绝 |
| 5 执行/Trace | 157a436d35a845a25fd56e66bd05e69fa6aad804 | 单指标alias物理绑定、failure_trace.py、dependency_agent、Memory Observe、SSE分类 |
| 5 Trace兼容修正 | a37c0728deacf1bc01a6e1458ac3826a2d570e2e | 预执行来源拒绝保持工具trace=[]，诊断另存failure_trace |
| 6 固定源码实验 | e1a815c2cc57bdfc3789d0282da8bd255dde97c2 | 性能/故障、16/185/22、新版基线、保留集、综合回归与独立核验 |

命令详见 [RUN_COMMANDS.md](RUN_COMMANDS.md)，包括环境、快照、实际pytest集合与原始输出位置；完整过程见 [EXPERIMENT_LOG.md](../memory_rl/EXPERIMENT_LOG.md)。后续复现使用新的output/label，工具禁止覆盖已有证据。BGE使用本地离线实际embedding，7项资产SHA已核验；模型 `BAAI/bge-small-zh-v1.5` revision `7999e1d3359715c523056ef9478215996d62a620`，权重95,827,648字节，SHA `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`。权重在models目录、被Git忽略，未改runtime/private配置。

## RAG 基础能力

原系统有可靠的来源句柄、SHA/locator校验及来源内检索接口；尚不能称复杂问题的来源选择与完整语义归属全面可靠。两级来源导航已实际比较，负结果保留在独立原型，未进入生产。标题命中不作为答案充分证据。

新集合为合成业务原生PDF/TXT、34来源、开发24题和保留8题，不使用隔离官方Gold。表格、跨页、缺证据等召回输入已冻结；更新/删除及行预算另用生命周期/Scope机制检查，不混入召回分母。必要文字证据标注相对容易，以下满召回不能解释成复杂问答全部正确。

| 策略/集合 | 必要证据完整 | Source/Evidence Recall@K | MRR/NDCG | 无关来源比例 |
|---|---:|---:|---:|---:|
| A BM25 开发，前/后 | 21/21 | 1/1 | 1/1 | .645833 |
| B BM25+真实BGE 开发，前/后 | 21/21 | 1/1 | 1/1 | .645833 |
| C 来源导航开发 | 21/21 | 1/1 | 1/.975982 | .708333 |
| A/B 保留，前/后 | 7/7 | 1/1 | 1/1 | .645833 |
| C 保留 | 7/7 | 1/1 | 1/.975982 | .708333 |

B开发6/24、保留2/8实际执行Dense；18/6纯英文按已有中文embedding守卫跳过，不把策略名称当作逐题Dense执行证据。A/B前后64个成对观测内容评分变化0、来源SHA/locator保持、错误complete声明0。C增加噪声、开发3题排序变差，保留也没有收益；未据保留结果调参。逐题前后分数、document/chunk ID和计时见 [final-verification-v2.json](runs/final-verification-v2.json)，详细候选见rag-*-before/final.json；初版审计ID字段误命名已纠正，旧产物保留。

两类证据改善是多行枚举的声明链审计，以及跨页/多来源版本范围。Scope明确列出已扫/已供给/未检查页、受检与匹配行、未审核条件、预算，并区分五类状态。只扫40页中的邻页不会声明全页完整；扫完8页但只供给6页明确预算不足。行审计拒绝重复ID、外来来源/修订混用，全部检查行与匹配行可追踪。完整性只对声明页范围/供给行链成立；语义条件、表头/单位、OCR质量仍须已有专门审核，页全覆盖不自动变成整题充分性证明。

答案正确率、EM、忠实度、回答引用正确率均 **not_run**；本轮没有付费生成模型复跑。检索SHA/locator不是生成引用正确率。真实扫描件OCR、多语言encoder、复杂跨页表格归属与多文档语义冲突仍有历史限制。

## Agent 执行与安全

统一Trace记录首个实际失败、阶段、类别、恢复后状态及独立整题验证未知；协议/规划、来源绑定/版本/参数、证据不足、执行和provider/transport可以区分。Trace可随Memory Observe记录，子任务成功或最终status=ok不自动赋整题成功reward。分类覆盖已测试路径，没有全面重写恢复器，也不保证所有旧异常已经结构化。

SQL AST/authorizer、物理Schema/原scope重编译、独立SQLite执行与行比较、原页/原行重放均保留，不用模型自评替代执行验证。单指标显示alias失败已修复且旧断言通过；多指标/不一致物理聚合不放宽。真实AVG请求被SUM计划保留的问题仍存在，已在起点快照复现，是底层语义一致性缺口。

原始混合写入/绕过意图在Memory、历史改写、规划、执行前整体拒绝，HTTP/SSE、低层SQL、旧CrossSource/DAG一致；临时库SHA前后不变，合法删查询过滤与引号内数据不误当DELETE。26项安全/API检查通过；实际“删除地区限制”返回合法上下文澄清，见legal-scope-edit.json。7项综合失败保留，未改断言或Gold：

- 2项原reset缺追问上下文/验证SQL快捷路由与旧期望冲突。
- 2项旧恢复测试插桩丢with_audit、snippet已经含条件的旧假设；起点复现。
- 2项历史测试要求危险请求部分执行，与本轮明确整体拒绝契约冲突。
- 1项AVG/SUM实际语义错误；起点复现。

综合相关集合533passed/7failed/12subtests，不宣称全项目全绿；原M1-C机制加新检查444passed/2历史failed。另SSE故障1passed：超时后worker仍活跃并持许可，事件如实记录worker_cancelled=false；尚未实现后台取消/断连持久审计。详细失败位置、原始堆栈、起点复现见 [SAFETY_AND_RELIABILITY.md](SAFETY_AND_RELIABILITY.md) 和runs中的txt/xml。

## 性能、成本与 Memory 兼容

真实隔离SQLite/KnowledgeStore路径，10/100/500原生页、1千/1万/10万行，12次热请求、4线程24并发。文档热检索P50/P95依次3.98/4.23、103.44/117.63、699.71/712.11ms；500页并发P95为4182.21ms。SQL三规模P50为4.60/3.93/10.69ms，串行与并发参考SUM均一致。正常测量请求失败0；重启hit一致、替换旧pin拒用，锁15秒后显式失败、缺索引/损坏SQLite显式失败、fake503归provider_transport。并发更新单版本片段由独立生命周期测试覆盖，未假称压力基准覆盖所有竞争窗口。全部QPS、磁盘、RSS、增量及逐请求值见 [PERFORMANCE.md](PERFORMANCE.md) 与performance-final.json。

同规格100来源300chunk的来源内检索读取300→3chunk，8次热中位约4.00→1.87ms；没有跳过SHA或缓存旧答案。全库查询仍重建BM25，500页约0.7秒是剩余瓶颈。纯本地性能进程RSS约232MiB；BGE预热125片段8.322秒、进程RSS约1.12GiB，未提高召回。性能是非独占当前环境观测，没有生成模型端到端耗时或稳定最大吞吐声明。付费调用、生成Token及API费用0，不继承M1-C剩余48次额度。

新固定源码重新建立兼容基线：原16题A10/16、B14/16保持；185题A/B各177→178，仅整体拒绝safe-09改善，原成功0退步且开关SQL/rows/status一致。22题A9/22、B22/22、C22/22保持，23候选、20验证、19晋升、0错误晋升；来源失效/撤销/冲突/跨scope与开关机制保留。原验证器“逐题完全不变”断言退出1并保留；新增独立验证以通用整体拒绝收据验证改善、冻结Hash、DB及源码稳定，不用case ID白名单。该变化来自安全基础设施，不是Memory收益。

M1-C历史真实模型四组7/7/8/9保持为原版本证据，仅审计、不重跑，不能当作F1当前模型成绩。池仅1条合法经验、B/C上下文相同，Fixed收益未成立。

## 训练前判断与下一研究问题

当前可以记录候选、选中动作、实际规划/执行结果及部分独立反馈；真实决策状态/恢复动作仍有限，failure_trace的unknown需要任务独立验证器补足。只对确有实质不同合法候选的配对试验，才可能把失败归因于记忆选择；现有M1-C差异首先受模型JSON/DSML协议、来源绑定及底层语义问题影响。不能用测试数量或B2静态/形成相同分数宣称策略学习。

**PPO Controller训练条件未成立。** 下一阶段优先建立多个真实成功、参数化且有版本约束的跨源经验，构造含无记忆/错误候选拒用/合法不同候选的独立难任务，冻结后比较Fixed、Oracle与普通提示的可归因reward差异；先补协议解析、AVG/SUM一致性及整题验证诊断。若研究模型改善，需要新预算授权和任务/Token/费用硬边界。本轮结束等待审核，再决定M1-C改进或M2，不执行训练。

输入/scorer与全部后端源码SHA、工具/新增测试、报告及原始日志Hash归档在 [FINAL_ARTIFACT_MANIFEST.json](runs/FINAL_ARTIFACT_MANIFEST.json)。密钥、真实.env、原始私有资料、未跟踪Qimem、权重二进制不进入提交/清单；权重只记录固定公共资产Hash。docs/ACCEPTANCE.md仅新增F1实际证据，不标记整体赛题完成。
