# 第六轮程序包与现场服务

本轮已固定源码交付，真实效果以 `ROUND6_FINAL_SOURCE_AUDIT_20261002.json` 为准，不以启动成功替代模型准确率。长期四核心目标保持 active。

- 程序ZIP：`D:/ICT8-Backups/ICT8-program-20261002-round6-quality.zip`，70,445,202 bytes（约67.2 MiB），717个文件。
- 解压验证：`D:/ICT8-Backups/ICT8-program-20261002-round6-quality.smoke.json`，12/12检查通过。包括独立新目录启动、无模型默认运行、Dense混合检索、前端、15份样例ID/metadata/原件SHA、真实SQL29584、普通DOCX/PDF问答、实际跨源公式及页来源。使用本机已有Python依赖，不能称洁净OS安装或真实模型全量通过。
- ZIP包含程序、必要小型样例与公开模型资产；排除runtime、真实凭据、Git历史、用户prototypes、历史PPT/Word材料和外置大型官方原件。正式数据另在 `D:/ICT8-OfficialDatasets`，对应恢复工具与manifest保留。
- `http://127.0.0.1:8030`已重启到当前代码，健康检查正常；模型为gpt-6-luna，检索为BM25+本地Dense融合，配置告警为空。
- 完整本地恢复备份将单独逐文件SHA校验，最新位置以 `D:/ICT8-Backups/LATEST-COMPLETE.txt` 为准。该备份包含本地runtime凭据，只用于本机恢复，不能公开上传。

最新程序源码与评测证据提交为 `9cc6acb`；本文件是其后的交付记录，不改变生产源码或模型成绩。
