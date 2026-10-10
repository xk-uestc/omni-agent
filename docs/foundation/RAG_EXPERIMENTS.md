# F1 RAG 实验记录

基线A开发24题：必要源Recall1.0，必要片段Recall1.0，MRR/NDCG1.0，annotated evidence21/21；无关来源比例.645833。此集合在相关证据召回上较易，挑战主要是噪声/范围/充分性，不能以满召回宣称复杂RAG正确。P50/P95 11.298/16.968ms，无模型生成，SHA/locator一致，错误complete声明0。原始逐题候选、审计、计时见runs/rag-A-dev-before.json。

冻结后不为制造新架构收益修改任务或评分；如果C无明显优势，保持flat默认。B真实embedding和C结果将在各自运行后追加。来源变更、删除、完整记录预算及故障用额外生命周期/证据scope实验，不混入Recall分母。

B已实际运行：开发24题中6题执行BGE（中文/混合）；18英文题按已存在语言守卫跳过。Source/Evidence Recall仍1，21/21 annotated complete，无关来源比例仍.645833，P50/P95 13.154/65.519ms。无新增召回收益；6次实际Dense有向量余弦、RRF和模型identity审计。向量预热和逐题原始结果见runs/rag-B-dev-before.json，不把英文跳过称作Dense通过。

权重下载及7项资产SHA核验见runs/bge-assets.json；BGE revision 7999e1d3359715c523056ef9478215996d62a620，model.safetensors SHA354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026。测试环境/tmp/f1-bge-venv通过.pth复用已安装测试和Torch环境，没有重复安装；实际版本写入最终环境记录，不能冒充历史Windows同版本性能。

C开发对照（6来源、同20候选/4输出）：必要证据召回仍1.0，无关来源.708333（A/B .645833），NDCG .975982（A/B 1.0），3项单题相关排序变差；C+BGE同样结果，6项实际Dense。证据不支持采纳：已从生产KnowledgeStore移除hierarchical参数和导航模块，仅保留tools/prototypes/foundation_source_navigation.py与隔离wrapper用于可复现研究。生产flat默认和调用链不变。移出后重新执行C原型结果相同，日志rag-C-prototype-dev.json。原始中间生产草案结果亦保留，不拼接最好值。

存储改进：records(document_id)下推SQL WHERE，Dense仅SELECT请求key、每批≤500，并校验读取向量；逻辑删除仅内部API、强制expected SHA、事务级删除chunk、保留共享不可变原件与历史资产。7项最终测试通过（初8项含已移出生产的导航试验）。并发检索/替换输出单版chunk、重启/删除旧句柄拒用、请求向量损坏拒绝、无关损坏缓存不加载、单来源不反序列化别的损坏chunk已实跑。

100文档/300chunk同规格单来源热查询：旧中位约4.00ms，新约1.87ms；新仅3chunk被反序列化。8次非独占环境观测，不能外推稳定全库提速或模型响应改善。完整数值与源码Hash见storage-after.json。
