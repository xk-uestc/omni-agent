# F2 困难 RAG 评测集与 No Change 基线

2026-10-11 先以开发源族做探索，必要证据完整仅6/21可答题，Evidence Recall .4325；来源Recall .8095。错误来源绑定命中6/24，证据不足查询有检索污染3/3。困难结构得到运行证据后，冻结新集。冻结后才进行一次A/B? No Change BM25基线，期间未改检索算法。

正式数据位于 `benchmarks/foundation_f2_20261011/`，48个问题、30份文档，均为自行编写的合成业务材料，不来自比赛Gold或真实私有语料。24题开发集来自cedar/harbor/meadow来源族，24题保留集来自island/orchard/summit；每族8题。来源文档有重复标题和年份、12页高术语重复手册、含30行且跨三页重复表头的表格、同名异机构实体、否定/排除、中文口语/混合查询、无证据拒答。索引按split只加入该split来源，任务不会看到另一个split的文档。

评分脚本只审计实际 KnowledgeStore 解析与检索结果，Gold literal在冻结前逐个核对原始文档内容，SHA与代码Hash列于manifest。输出每题source/evidence Recall、MRR/NDCG、Top-K证据文本/chunk/page、必要证据集完整度、明确列出的错误绑定来源、无证据查询的检索污染、原件SHA/locator、P50/P95和索引规模。Answer EM、忠实度、引用正确率均not_run；完整检索证据不等于答案正确。

## 冻结集 No Change 基线

固定无Dense动态BM25，top_k=4，同一评分器、解析器和字段；Python 3.12.3、SQLite 3.45.1，未调用模型。当前只有A组No Change，不存在B组优化结果。

| Split | 可答题 | Source Recall@4 | 必要证据 Recall@4 | 完整证据题数 | MRR | NDCG | 错误来源绑定题数 | 无证据污染题数 | P50/P95 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dev (24) | 21 | .8095 | .4325 | 6/21 | .3571 | .3941 | 6/24 | 3/3 | 14.17 / 36.56 |
| Retained (24) | 21 | .8095 | .4167 | 6/21 | .3333 | .3750 | 6/24 | 3/3 | 14.40 / 39.30 |

所有48题命中引用SHA/locator均可与入库原件核对。开发探索结果为24题/15来源/189chunk；冻结正式开发为同一批题与解析，原始两份分数一致（计时因系统调度有小幅浮动）。此集合已明确区分F1中过易的基线；困难来自同名同标题绑定、长文分散条件、枚举空间和多页表格，不以自然语言题目改写冒充跨源独立样本。No Change逐题和环境证据见 `docs/foundation/runs/f2-{dev,retained}-before.json` 及对应txt；exploration为 `f2-dev-exploration.json`。输入Gold与scorer Hash见benchmark manifest，F1输入与Gold未修改。

复现：

```bash
/tmp/omni-m1a-venv/bin/python tools/freeze_foundation_f2.py
/tmp/omni-m1a-venv/bin/python tools/validate_foundation_f2.py
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split dev --output docs/foundation/runs/f2-A-dev-before.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split retained --output docs/foundation/runs/f2-A-retained-before.json
```

输出路径须不存在。冻结后没有修改文档、题目或评分器来追求分数。本节和保留集成绩只用于最终一次对照，不据此选排序超参。
