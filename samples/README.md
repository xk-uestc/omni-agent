# 可复现的实际文件样本

本目录由 `python tools/create_sample_corpus.py --ingest` 重建，全部为本项目生成的合成演示资料，按 CC0-1.0 提供。不能表述为真实企业资料或公开基准数据。

包括实际 PDF、DOCX、XLSX、TXT、Markdown、中文扫描 PNG、颠倒扫描 PNG 和无文字层扫描 PDF。`manifest.json` 记录文件哈希、格式、大小和来源声明。扫描图片没有隐藏答案文字，识别必须由真实 OCR 完成。

Office 样本显式声明表头、单位和数据行数；原始文件保留并通过知识库 API 可下载。Excel 文件不包含未重算的公式。公式示例在 Word/PDF 文本内，由安全公式模块识别并进行有来源的参数绑定。

知识库索引位于本项目 `runtime/knowledge`，可删除后按上述命令重新生成；原始样本及生成程序才是交付输入。
