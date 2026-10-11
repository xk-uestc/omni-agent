# F3 真实运行结果（2026-10-11）

本项目编写的冻结困难夹具，不是公开/官方基准。Gold、scorer、输入和 Top-K=4 均未修改。所有真实生成模型/答案准确率 `not_run`，paid API calls=0。

| 开发集阶段 | Top-K Evidence Recall@4 | 完整证据 /21 | 错误绑定 /24 | 无证据污染 /3 | 查询 P50/P95 ms |
|---|---:|---:|---:|---:|---:|
| F2 持久 BM25 | .4325 | 6 | 6 | 3 | 历史见 F2 日志 |
| F3 来源条件绑定 | .5040 | 7 | 0 | 3 | 38.10/85.23 |

来源绑定：24题逐题结果 `runs/f3/binding-dev-final.json`；改善/退步 `binding-deltas.json`。必要证据召回2题改善，无下降；7题完整不是达到本轮目标。来源正确不能证明不存在无证据污染。第一次与最终开发测量分别保留；新增机构冲突检查不改变本集宏观分数。

命令（工作目录仓库根；Python `/tmp/omni-m1a-venv/bin/python`）：

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split dev --output docs/foundation/runs/f3/binding-dev-final.json
# HTTP TestClient 在沙箱内停滞，原日志保留为 binding-tests.txt；停止进程后在授权环境重跑：
timeout 120s env PYTHONPATH=ict-track8:tools ICT8_DEMO_MODE=1 ICT8_DB_PATH=/tmp/f3-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest ict-track8/tests/test_f3_source_binding.py ict-track8/tests/test_foundation_retrieval.py ict-track8/tests/test_foundation_safety.py ict-track8/tests/test_evidence_coverage.py -q
```

原始 stdout：`runs/f3/binding-dev-final.txt`、`binding-tests-verified.txt`。额外检索0、额外原页读取0；绑定在现有正文集合上执行，新增持久索引0。完整性能与所有最终回归待候选冻结后补录。

来源绑定回归：47通过、12子测试；第一次环境外运行因未初始化显式测试库46通过/1失败，保留 binding-tests-final.txt；修正演示库测试配置后验证。

来源内关联开发结果：Recall@4 `.5754`，完整 `10/21`，错误绑定 `0/24`，无证据污染 `3/3`。三题Recall .5→1，必要证据0退步，逐题 `runs/f3/linked-deltas.json`，最终 scorer 结果 `linked-dev-final.json/txt`。MRR `.6468→.5754` 下降（首条可能为对象声明而非 Gold literal），nDCG `.6217→.6477`；不隐藏这一排序代价。新关联只有0额外检索/0原页读取，增加来源校验及当前索引正文导航；持久索引0增量。

命令同上 evaluator，输出 `linked-dev-final.json`；pytest 文件为 `test_f3_linked_evidence.py test_f3_source_binding.py test_foundation_retrieval.py test_knowledge_store.py test_foundation_safety.py`，42通过，日志 `linked-tests-final.txt`。首次预算测试1失败见 `linked-tests.txt`。

第一项远端提交 `bee7f2ef2dd01b4cb6fb8c5c3241b47aa0b91e2f`。

第三项开发冻结：旧 F2 scorer `frozen-topk-dev.json/txt` 为 `.5754`、10/21完整、0/24错绑、无证据污染3/3。生产 `answer()` 的单独证据测量 `pipeline-dev-scopes.json/txt`：

| 证据通道 | Recall | 完整 /21 |
|---|---:|---:|
| 原始 Top-K=4 | .5754 | 10 |
| 原始 Top-K + 单独有界 bundle | .8571 | 16 |
| 实际 answer 入口筛选后证据 + bundle | .7619 | 15 |

六道表格题实际进入新增路径，各30条原文清单/10条匹配行；六题均完整，1额外检索、显式编号3页/机构解析5页。`dossier_preparation` 收据另确认原有答案准备流程取得完整原页，重新构造成本再增加1检索和3–5页。确定性审核器测试证明引文、两页行、独立审核和重放实际接通，真实模型not_run。每次prepare上限6页/32000字；并非整个有审核器答案的累计读取上限6页。

生产热查询72次（每题3次）：P50/P95 54.29/148.84ms（含已有问题子句导航）；生成器准备文本P50 1366、最大3615字，真实生成器可见0字。24题已有入口总检索1–3次，新增表链6题各1次，额外原页读取总24页；模型前重构/最终重放成本单列而不冒充首次预算。

结构性剩余失败：长册3题仍只取周期，没有编号→第9页例外的明确关系；口语别名3题在实际 answer 的既有 question-part/词项门槛中被筛空（初始Top-K仍有部分证据），其中一题Top-K本来完整，因此 answer 完整15而Top-K+bundle为16。未为这六题无限调整规则。无证据题在检索仍3/3污染。3道别名题实际无证据错误拒答；六道表格题无审核器时待审核拒答，不能声称真实问答成功。

复现：

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f3_pipeline.py --split dev --output docs/foundation/runs/f3/pipeline-dev-scopes.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split dev --output docs/foundation/runs/f3/frozen-topk-dev.json
```

第二项远端提交 `efa941c8de8db9a6809a3ae30f633412ef62ff3c`。第三项提交后冻结生产实现，再运行保留集及完整回归，不依据其分数改算法。
