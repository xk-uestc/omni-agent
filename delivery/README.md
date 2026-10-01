# 赛题八当前交付材料

| 文件 | 用途 |
|---|---|
| 01-design-report.docx / .pdf | 设计报告；封面后两页为核心亮点预览 |
| 02-technical-specification.docx / .pdf | 技术实现、评测指标、复杂度和复现 |
| 03-finals-technical-report.docx / .pdf | 决赛深化准备稿；无虚构初赛反馈 |
| 04-innovation-statement.docx / .pdf | 实用创新与转化价值；PDF不超过5页 |
| 05-anonymous-defense.pptx | 16页匿名答辩；可编辑表格、OCR图表与架构图 |
| architecture.png | 当前系统架构（原生PPT图的实际渲染） |
| requirements-tested.txt | 当前实测的直接依赖版本 |
| ENVIRONMENT.json | Python、平台、全部已安装包和缺失的可选依赖 |
| SOURCE_MANIFEST.json | 材料使用的赛题、原始报告与架构证据SHA |
| MATERIAL_VALIDATION.json / PDF_EXPORT.json | 实际PDF分页、页数上限和字符/边界检查 |

指定`gpt-6-luna`真实API返回401，尚无真实模型成绩；开发题结果不代表官方未知题准确率。组委会模板尚未提供，当前使用自定义匿名排版。

代码、报告和PPT共用项目已有证据。对应Markdown和构建脚本保留，更新实验后可重新生成。Word对应PDF由本机WPS真实导出，PPT经过Artifact Tool的结构、布局、原生图表数据与导入校验。当前尚未在原生Microsoft PowerPoint中验证。

本目录的四份Word/PDF及16页PPT已按当前证据重新生成，包含524项回归、差旅新Schema、真实PDF目录、统一澄清及十项故障恢复（首次4/10，修复后10/10）。第五类已实际执行检索→来源事实→Excel→阈值比较，与HYBRID_ACCEPTANCE_REPORT一致。真实44题预检仍401，题目未执行。SOURCE_MANIFEST记录生成时来源哈希，首次失败保留。

文档首选renderer因缺少LibreOffice soffice.exe不可用，当前PDF使用本机WPS真实导出，再逐页栅格化检查；不声称已用首选renderer或原生Microsoft PowerPoint验证。实际页数见PDF_EXPORT及MATERIAL_VALIDATION。未知题、指定模型401、真实初赛反馈和官方模板边界仍然保留。

完整公开资产压缩包仍需要先安装Python依赖；真实模型配置单独保留于本机runtime，不随包传递。
