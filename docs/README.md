# 文档状态说明

本目录的 `ICT_TRACK8_*`、`NL2SQL_*` 历史报告随源码整体迁移，用于继承思路、解释旧实验和实现边界；其中 D 盘路径、8014 生产联调和历史成绩不表示独立项目已完成这些验收。

本项目的当前事实以以下文件和真实运行结果为准：

- `ACCEPTANCE.md`：全部赛题要求与未完成项。
- `BASELINE_MANIFEST.json`：迁移时逐文件校验与原始版本。
- `SAMPLE_INGEST_REPORT.json`：实际文件、OCR 和切片入库记录。
- `INDEPENDENT_ACCEPTANCE_REPORT.json`：本地合成样本的问数、文档及跨源验收结果，不能当作公开基准成绩。
- `HYBRID_ACCEPTANCE_REPORT.json`：实际BGE与BM25/RRF的当前验收。
- `CHINOOK_RULES_REPORT.json`、`SCHEMA_SCALE_REPORT.json`：公开数据库开发题与合成干扰表。
- `ROBUSTNESS_REPORT.json`：真实OCR的原图/增强成对结果。
- `MODEL_API_PROBE.json`：用户指定gpt-6-luna的最近真实鉴权结果。
- `DOMAIN_TRANSFER_FIRST_RUN.json`、`DOMAIN_TRANSFER_REPORT.json`：新差旅Schema首轮6/8与修复后8/8，明确为自编审计/开发回归。
- `RELIABILITY_UPDATE_20261001.md`：规划上下文、聚合覆盖与原文件行号修复，以及本轮回归口径。
- `history/REAL_MODEL_REPORT_PRE_LUNA.json`：旧模型历史实验，不是gpt-6-luna验收。

正式材料已在delivery制作并校验，为ade1b60快照。本轮代码/评测另见可靠性更新；最终定稿需更新材料及完整独立评测，不用快照替代新实验结果。
