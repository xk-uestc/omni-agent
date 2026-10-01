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
- `history/REAL_MODEL_REPORT_PRE_LUNA.json`：旧模型历史实验，不是gpt-6-luna验收。

最终技术文档、PPT、创新自述及完整独立评测仍须在功能和效果稳定后制作，不用历史报告替代。
