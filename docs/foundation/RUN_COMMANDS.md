# F1 运行命令索引

以仓库根为cwd；输出文件要求不存在以防覆盖证据。所有模型provider为空；BGE设置HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0。/tmp/f1-bge-venv通过.pth复用已安装测试/Torch依赖（3.12环境），没有重复安装；初次venv ensurepip不可用后使用现有解释器路径与依赖映射，运行版本见JSON。权重下载是唯一模型资产网络操作，按manifest固定SHA核对。

```bash
/tmp/omni-m1a-venv/bin/python tools/fetch_foundation_bge.py
/tmp/omni-m1a-venv/bin/python tools/freeze_foundation_rag.py
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_rag.py --output docs/foundation/runs/rag-A-dev-before.json
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 /tmp/f1-bge-venv/bin/python tools/evaluate_foundation_rag.py --dense --output docs/foundation/runs/rag-B-dev-before.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_navigation.py --strategy hierarchical --output docs/foundation/runs/rag-C-prototype-dev.json
/tmp/omni-m1a-venv/bin/python tools/measure_foundation_storage.py --output docs/foundation/runs/storage-after.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_m1b1.py --label f1-memory-final-20261010
/tmp/omni-m1a-venv/bin/python tools/evaluate_memory_formation_m1b2.py --label f1-formation-final-20261010
/tmp/omni-m1a-venv/bin/python tools/verify_memory_formation_m1b2.py --label f1-formation-final-20261010
/tmp/omni-m1a-venv/bin/python tools/verify_memory_m1b1_evidence.py --label f1-memory-final-20261010
/tmp/omni-m1a-venv/bin/python tools/measure_foundation_performance.py --output docs/foundation/runs/performance-final.json
/tmp/omni-m1a-venv/bin/python tools/verify_foundation_final.py
```

原记忆验证器预期退出1：逐题完全不变断言因安全改善不成立；新F1独立审计退出0。准确输出重定向与修复沿革见memory_rl/EXPERIMENT_LOG.md。snapshot命令如下，未切换branch：

```bash
mkdir -p /tmp/f1-start-snapshot
git archive 94a9926de2cee14c014a9c6e93c63112407d7760 ict-track8/backend ict-track8/tests ict-track8/data | tar -x -C /tmp/f1-start-snapshot
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_snapshot.py --snapshot-root /tmp/f1-start-snapshot --source-commit 94a9926de2cee14c014a9c6e93c63112407d7760 --split retained --output docs/foundation/runs/rag-A-retained-before.json
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 /tmp/f1-bge-venv/bin/python tools/evaluate_foundation_snapshot.py --snapshot-root /tmp/f1-start-snapshot --source-commit 94a9926de2cee14c014a9c6e93c63112407d7760 --dense --split retained --output docs/foundation/runs/rag-B-retained-before.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_rag.py --split retained --output docs/foundation/runs/rag-A-retained-final.json
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 /tmp/f1-bge-venv/bin/python tools/evaluate_foundation_rag.py --dense --split retained --output docs/foundation/runs/rag-B-retained-final.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_navigation.py --strategy hierarchical --split retained --output docs/foundation/runs/rag-C-retained.json
```

最终综合回归确切集合：

```bash
env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= timeout 180s /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_retrieval.py ict-track8/tests/test_foundation_evidence_scope.py ict-track8/tests/test_foundation_failure_trace.py ict-track8/tests/test_foundation_safety.py ict-track8/tests/test_knowledge_store.py ict-track8/tests/test_dense_retrieval.py ict-track8/tests/test_evidence_coverage.py ict-track8/tests/test_source_answer_dossier.py ict-track8/tests/test_native_row_selection.py ict-track8/tests/test_pdf_page_index.py ict-track8/tests/test_dynamic_source_binding.py ict-track8/tests/test_fusion_source_constraints.py ict-track8/tests/test_fusion_history.py ict-track8/tests/test_sql_document_binding.py ict-track8/tests/test_omni_agent.py ict-track8/tests/test_omni_query_stream.py ict-track8/tests/test_nl2sql_v2.py ict-track8/tests/test_nl2sql_pressure_regressions.py ict-track8/tests/test_api_security.py ict-track8/tests/test_evidence_recovery_round5.py ict-track8/tests/test_document_transport_recovery.py ict-track8/tests/test_fault_recovery.py --junitxml=docs/foundation/runs/foundation-final-tests.xml > docs/foundation/runs/foundation-final-tests.txt 2>&1
```

真实本机HTTP/SSE TestClient需socket权限；初始受限socket测试挂起的部分输出baseline-tests-configured.txt被保存，随后终止任务自己的挂起进程，使用允许本机socket的完整日志替代，不称该部分输出是有效测试成绩。

追加SSE fault命令：

```bash
env ICT8_DB_PATH=/tmp/f1-api.sqlite ICT8_PLAN_URL= ICT8_PLAN_PROVIDER= ICT8_GENERATION_PROVIDER= ICT8_MANUAL_RETRIEVER_URL= ICT8_DENSE_MODEL_PATH= timeout 30s /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_stream_fault.py > docs/foundation/runs/stream-fault-test.txt 2>&1
```

最终独立核验现在输出final-verification-v2.json，修正初版将document ID命名为chunk ID的问题；旧版保留，指标没有变化。复跑应使用新输出目录/label，不能覆盖已有证据文件。
