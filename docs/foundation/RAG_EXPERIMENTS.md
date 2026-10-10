# F1 RAG 实验记录

基线A开发24题：必要源Recall1.0，必要片段Recall1.0，MRR/NDCG1.0，annotated evidence21/21；无关来源比例.645833。此集合在相关证据召回上较易，挑战主要是噪声/范围/充分性，不能以满召回宣称复杂RAG正确。P50/P95 11.298/16.968ms，无模型生成，SHA/locator一致，错误complete声明0。原始逐题候选、审计、计时见runs/rag-A-dev-before.json。

冻结后不为制造新架构收益修改任务或评分；如果C无明显优势，保持flat默认。B真实embedding和C结果将在各自运行后追加。来源变更、删除、完整记录预算及故障用额外生命周期/证据scope实验，不混入Recall分母。

B已实际运行：开发24题中6题执行BGE（中文/混合）；18英文题按已存在语言守卫跳过。Source/Evidence Recall仍1，21/21 annotated complete，无关来源比例仍.645833，P50/P95 13.154/65.519ms。无新增召回收益；6次实际Dense有向量余弦、RRF和模型identity审计。向量预热和逐题原始结果见runs/rag-B-dev-before.json，不把英文跳过称作Dense通过。

权重下载及7项资产SHA核验见runs/bge-assets.json；BGE revision 7999e1d3359715c523056ef9478215996d62a620，model.safetensors SHA354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026。测试环境/tmp/f1-bge-venv通过.pth复用已安装测试和Torch环境，没有重复安装；实际版本写入最终环境记录，不能冒充历史Windows同版本性能。
