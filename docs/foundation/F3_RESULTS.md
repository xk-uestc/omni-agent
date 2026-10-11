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
