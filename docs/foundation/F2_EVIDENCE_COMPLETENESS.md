# F2 复杂证据集合与完整性验证

2026-10-11仅在冻结开发集6个表格任务上，对比冻结Top-K、F1 `build_dossier` 与受限表链补全。F2源文件、Gold和评分器均未改；候选选择先完成，再由Gold核算。输入源Hash运行前后相同，未调用模型。

| 方案 | 必要证据平均Recall | 完整任务 | 读取成本与Scope |
|---|---:|---:|---|
| A：原Top-K（4 chunks） | .0417 | 0/6 | 无额外页扫描；原检索已耗时列于逐题记录 |
| B：F1 `build_dossier` | .7222 | 4/6 | 每题扫描7–15页、最多供给6页；所有目标任务Scope报告 `budget_exhausted`，候选来源分摊页预算 |
| C：实体/编号绑定后的表链 | 1.0000 | 6/6 | 每题1–2次Top-K检索，扫描3–4个完整原生PDF页；30行唯一ID、连续页、重复表头、单位、筛选列、脚注与来源SHA逐项核验 |

C方案复用 `page_scope` 和 `row_scope`，不写第二套scope状态机。用户明示批次号时，从原Top-K来源中定位完整匹配行ID并扫描该表全部页；用户只给机构时，先核验命中文件页内的机构→对象代码，再以该代码做一次有界检索，随后仅接受唯一候选表并扫描其完整页链。表ID重复、原件更新、页预算不足、代码/来源歧义都会失败关闭。Scope仅声明所列来源与页的完整性；`semantic_sufficiency`仍是 `not_proved_by_page_coverage`，不会由扫描完成自动推断整题正确。

C比B降低页读取数量并补全困难证据，但增加第二次精确检索与实体绑定核验，端到端本地时间不保证更快；逐题延迟、页清单、Gold literal、行ID、SHA和拒绝理由见 `runs/f2-evidence-scope-dev-final-v10.json`。没有整题生成模型预算，本实验不报告EM、忠实度或答案准确率，也没有验证生产路由采用该研究原型。C的收益是范围和原始行证据闭合，不是Memory或Planner模型效果。

复现命令（输出路径必须不存在）：

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_foundation_f2_evidence.py
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2_evidence.py --split dev --output docs/foundation/runs/f2-evidence-scope-dev-final-v10.json
```
