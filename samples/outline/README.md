# PDF目录审计样本

本目录8份实际PDF为本项目生成的合成CC0-1.0样本，包含9页；不是真实企业文档或公开基准。
输入、标题序列、正文层级路径、页码及必需告警在ict-track8/eval/outline_cases.json中预先编写；首次运行前即冻结。

首次2/8，修复后8/8；首次失败与各PDF的SHA256保存在docs/PDF_OUTLINE_FIRST_RUN.json，不覆盖。修复后的题为开发回归，不能称独立盲测或公开准确率。

复现：python tools/evaluate_pdf_outline.py。已有PDF保持原样，不在评测时重生成；缺失文件才由固定canvas输入重建。
docs/evidence/pdf-outline-review.png是所有9页正文区域的实际渲染拼图；仅用于检查样本可读性，不代表OCR实测。

主Demo的15份知识样本仍按samples/manifest.json入库，本目录不自动加入其中，避免把审计样本混入问答金标。
