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

## 冻结后正式对照与最终交付

生产冻结完整SHA：`67c4d3963d7078edeb9c1d5f7c58981caa072ffd`，已经推送并核对远端 memory。冻结后没有修改生产算法；最终只提交测试结果、验证工具和文档。

| 集合 / 通道 | F2 Top-K Recall | F3 Top-K Recall | F2→F3 Top-K完整 /21 | F2→F3错绑 /24 | Top-K+bundle Recall /完整 | 实际answer证据 Recall /完整 |
|---|---:|---:|---:|---:|---:|---:|
| 开发 | .4325 | .5754 | 6→10 | 6→0 | .8571 /16 | .7619 /15 |
| 保留 | .4167 | .5595 | 6→10 | 6→3 | .7024 /13 | .6071 /12 |

冻结48题对照：10题Top-K必要证据召回改善，0题下降，逐题见 `runs/f3/final-verification.json` 的 f2_pairs。开发表格6/6补齐；保留仅显式编号3/6补齐，另外3题机构→对象解析拒用。当前机构语法不覆盖“中心、研究所、监测局”，同时导致三个邻近机构错绑未解决；这是明确的泛化限制，没有依据保留集分数追加规则。两个split的无证据题Top-K污染仍各3/3。

F1旧32题：与 F2 相同 flat 策略，Recall全部1.0、完整21/21+7/7、来源/引用指标逐题一致。首次 hierarchical 命令因接口没有strategy参数失败（`f1-dev.txt`）；没有改旧scorer，按原flat配置复跑。

原记忆16题 A10/B14、185题 A/B178、22题 A9/B22/C22均逐题保持 F2 pass 向量；只读数据库、source前后SHA一致。22题候选精度20/23、原文抽取23/23、错误晋升0，B/C知识绑定和rows相同。旧 M1-B1 verifier 对更早 M1-A 的185题全向量相等断言仍失败，原stdout与verification.json保留；独立 F3 verifier 对当前F2基线全部相等且SQL/rows/status保持，不能把旧失败隐藏为全绿。22题原verifier通过，其中旧legacy字段引用历史路径；F3独立verifier另核验本次16/185。

最终相关回归：**934通过，6既有失败，12子测试通过，25警告**；测试范围含F3 17、foundation 50、dossier 35、native_row 78、memory 58、task_experience 16。各类别不是互不重复的总体成绩，也不与前面定向测试相加。六失败完整名单与F2相同（fusion reset、SQL followup route、两项unsafe部分执行旧断言、两项recovery插桩/snippet旧假设），见 final-regression.txt/xml；AVG/SUM与来源/SQL安全相关测试通过。测试临时改动了tracked demo_sales.sqlite，已恢复到进入任务时干净的Git版本，SHA收据 `demo-db-restoration.txt`。无新生产DB/真实.env进入提交。

### 成本

同环境、相同开发24题、每题3次热生产answer对照（`cost-before.json` / `cost-after.json`）：

| 项目 | 冻结前生产 | F3冻结后生产 |
|---|---:|---:|
| 热P50 / P95 ms | 23.79 /103.39 | 75.15 /159.82 |
| Python单查询分配峰值P50 bytes | 5862035 | 5862491 |
| Python单查询分配峰值最大 bytes | 6007574 | 10005512 |
| 现有持久检索索引 bytes | 1732608 | 1691648 |

P50约增加51ms，P95约增加56ms，单查询最大Python分配峰值增加约3.81MiB。tracemalloc与时延分开测；这是小型夹具的序列测量，不是生产容量/负载承诺。新增永久索引结构0；现有BM25因范围变化实际磁盘减少40960bytes，不将其说成稳定压缩收益。pipeline日志中的RSS增长约25–27MiB包含整套临时文档入库，不作为算法增量内存。

开发bundle：新增检索6次、首次原页读取24次，3–5页/题；generator准备文本P50/最大1366/3615字。保留bundle仅3题成功，机构语法失败3题读取0页；热answer P50/P95 40.20/129.44ms，准备文本P50/最大326/3613字。原页准备重构另每次1检索和相同读取，最终既有native quote replay另最多6页；真实审核/生成尚未run，不能把未发生的模型重放成本当作首次模型实测。真正生成器可见0字，答案准确率、付费模型效果、BGE新对照均not_run。

### 命令与原始证据

所有新产物哈希在 `runs/f3/ARTIFACT_MANIFEST.json`（不包括自身及redirected verifier stdout）。主要正式命令：

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split retained --output docs/foundation/runs/f3/frozen-topk-retained.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f3_pipeline.py --split retained --output docs/foundation/runs/f3/frozen-pipeline-retained.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_rag.py --split dev --strategy flat --output docs/foundation/runs/f3/f1-flat-dev.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_rag.py --split retained --strategy flat --output docs/foundation/runs/f3/f1-flat-retained.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label f3-final-m1b1-20261011
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label f3-final-m1b2-20261011
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/verify_memory_formation_m1b2.py --label f3-final-m1b2-20261011
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/verify_memory_m1b1_evidence.py --label f3-final-m1b1-20261011 # 原verifier失败保留
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/measure_foundation_f3_cost.py --backend-root /tmp/f3-baseline/ict-track8 --output docs/foundation/runs/f3/cost-before.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/measure_foundation_f3_cost.py --backend-root /home/viva/omni-agent/ict-track8 --output docs/foundation/runs/f3/cost-after.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/verify_foundation_f3.py
```

成本旧backend通过 `git archive bd666d5efdcedd5167c65136e190136114553a1b ict-track8/backend | tar -x -C /tmp/f3-baseline` 生成只读对照。原始CLI stdout、逐题metrics、native rows/pages/headers/units/dates、生产trace、dossier接入收据均随runs提交。最终pytest完整文件清单和命令在 `runs/f3/final-commands.txt`。

功能完成的范围：正文明确条件绑定、显式来源内关系导航、受限pipe原生表枚举补证，均已进入生产并在冻结任务证明证据改善。通用机构理解、无关系跨页、口语别名后置拒答、无证据污染、真实生成答案效果和全赛题验收均未完成。停止本轮，等待ChatGPT审核；未开始PPO/GRPO，未修改main。
