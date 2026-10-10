# M1-A 实验记录（2026-10-10）

所有实验使用源码基准 a4ed8bb…，运行时 HEAD 为审计提交 `f5dba53602b806acf27ebbdcf810bd7d919ad0d1`；新 evaluator/input 尚未提交但执行前 SHA 冻结，随本次结果提交一起提供。不使用生产数据/QiMem 数据库/外部模型，不改变 backend。日期使用 Asia/Shanghai。

| 实验 | 实际结果 | 原始记录 |
|---|---|---|
| 185 回合 No Memory 正确性 | 177/185，DB/source 前后相同；无时延重复测量 | `runs/context-no-memory-20261010.json`、`context-stdout.txt` |
| 历史源 hash 对比 | 21 文件不同，不能把历史 185/185 沿用至当前 HEAD | `runs/context-diagnostic.json` |
| 已记录依赖版本有限复现 | 14 探针含原失败会话前置，6/14，8 个失败全部仍失败；非完整重跑 | `runs/context-pinned-dependency-probes.json` |
| 16 个冻结 memory-sensitive A | 10/16，语义 0/4、方法/变更 0/2、控制 10/10 | `runs/m1a-memory-sensitive-no-memory-20261010/summary.json` 与每 task JSON |
| method 前置实际工具验证 | 1 次 DependencyAgent 真实执行 + 独立 SQL/文档/公式评分通过；未注入目标 | 同目录 `preparation.json`、`candidate-evidence.json` |
| scorer 反例 | 首次脚本无效变异 5/6；改 parameters 后 6/6；最终可复跑 CLI 6/6 | 同目录 `scorer-negative-checks*.json` |
| 初次合并现有回归 | 77 个源码用例后首个 HTTP 请求停滞，人工中断 exit 130；不宣称完整通过 | `runs/selected-regression-interrupted-stdout.txt` |
| 源码现有回归独立执行 | 77/77，1.66s | `runs/source-regression.xml`、`source-regression-stdout.txt` |
| pinned HTTP/SSE sandbox | 首个 health 请求停滞，中断 exit 130 | `runs/http-sse-sandbox-interrupted-stdout.txt` |
| 同一 pinned HTTP/SSE 环境外执行 | 14/14，0.76s，3 条弃用 warning | `runs/http-sse-escalated.xml`、`http-sse-escalated-stdout.txt` |
| Fixed Memory B / Oracle C | not_run：Memory Adapter 未实现 | summary 的 comparison_groups |
| 真实远程模型基线 | not_run：本轮无已批准 endpoint/模型/付费预算，未创建 remote provider | summary 的 real_model_baseline |

输入/scorer/fixture hash 全量在开发集 manifest 和 summary；数据库 SHA、原件/别名/源码版本前后、Python/SQLite、34 包锁、任务与 session ID、逐题响应/trace/SQL/参数/来源/耗时都有原始证据。模型调用/token 为 0；trace 工具计数不等于底层数据库调用次数。没有速度增益/官方成绩/已训练结论。

## 确切计分与诊断命令

```bash
/tmp/omni-m1a-venv/bin/python tools/evaluate_context_semantics_20261009.py --label memory-m1a-no-memory-20261010 --output docs/memory_rl/runs/context-no-memory-20261010.json --skip-latency
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_sensitive_m1a.py --label m1a-memory-sensitive-no-memory-20261010
/tmp/omni-m1a-venv/bin/python tools/verify_memory_sensitive_m1a_scoring.py --output-name scorer-negative-checks-final.json
/tmp/omni-m1a-pinned-venv/bin/python tools/diagnose_memory_m1a_baseline.py
```

测试命令（设置数据库至 /tmp seed，不触碰已提交数据；所有远程 provider 关闭）：

```bash
ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/selected-regression.xml

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_session.py ict-track8/tests/test_semantic_integration.py ict-track8/tests/test_dependency_agent.py --junitxml=docs/memory_rl/runs/source-regression.xml

timeout 120s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-pinned-venv/bin/python -m pytest -vv ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/http-sse-pinned.xml

timeout 45s env ICT8_DB_PATH=/tmp/omni-m1a-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= /tmp/omni-m1a-pinned-venv/bin/python -m pytest -q ict-track8/tests/test_api_security.py ict-track8/tests/test_omni_query_stream.py --junitxml=docs/memory_rl/runs/http-sse-escalated.xml
```

前三条测试命令按上述实际状态记录（合并/HTTP 被中断，所以相应 XML 不存在，不伪造）；第四条通过 require_escalated 执行后实际完成。对照说明 sandbox 环境相关性，不把未经堆栈诊断的 socket 根因写成已确认。
