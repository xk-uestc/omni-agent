# 文字质量程序更新

按用户要求，本轮只完善程序，不再制作PPT、Word或PDF。

- 文档分析和入库记录增加繁体、已知业务错字与不可识别字符告警。
- OpenCC 0.1.7 t2s用于BM25与实际BGE检索的繁简一致化；源文件、切片、引用、SHA不改写。向量缓存包含转换版本。
- 知识库页面增加文字质量检查和确认校正预览。`/api/v1/documents/text-quality`返回原文位置和SHA；`/text-repair`核对SHA及服务端候选ID，生成单独预览，不修改知识库。
- 默认不应用错字建议；需要逐项勾选，未知错字、金额、编号及人名不推测。代码及URL保留。
- 开发成对审计首次13/14，发现URL后中文标点被误识别为URL的一部分，修复后扩展至15/15。首次证据保留于TEXT_QUALITY_FIRST_RUN.json；当前见TEXT_QUALITY_REPORT.json。这不是独立盲测或通用中文纠错准确率。
- 现有真实gpt-6-luna预检401仍未解决，没有换用其他模型，也没有反复请求失效凭据。

程序包使用`python ict-track8/scripts/package_delivery.py --program-only --with-public-assets --output <ZIP>`，包含运行依赖清单、源码、测试、公开样本和Dense模型，排除本机凭据与参赛材料。下载后仍需安装requirements中的依赖。
