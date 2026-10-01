# 工程证据索引

当前独立项目为 `D:/ICT8-OmniAgent`。以下表格为最新已完成实测入口；下方原索引保留为历史说明，其中“最终”“当前”仅适用于对应报告的执行时点，不覆盖新报告。

| 文件 | 实测口径 |
|---|---|
| `ACCEPTANCE.md`、`coverage.json`、`CURRENT_ARCHITECTURE.md` | 全赛题状态、实际实现及剩余缺口 |
| `OFFICIAL_DATASET_ACCEPTANCE.md` | 三个推荐数据源的固定版本、恢复命令与实测边界 |
| `LOCAL_REGRESSION_REPORT.json` | 当前源码契约回归；不调用模型，不代表模型准确率 |
| `REAL_MODEL_THIRD_RUN_20261001.json` | 第三轮38/44、87次API完成；早于后续Schema/计数/单位及路由补丁 |
| `CHINOOK_MODEL_SECOND_RUN_20261001.json` | 原库人工开发题12/12严格模型；10非空、2空结果边界，非官方金标题 |
| `ADVENTUREWORKS_SQLITE_PORT_SECOND_RUN_20261001.json` | 官方CSV三表SQLite移植4/6；两题19119组被100行上限截断，非原生PG |
| `OHR_BENCH_MODEL_VERIFIED_SCORE_20261001.json` | 官方原题12题资源受限pilot：1实质回答、9拒答、2回退，EM0/12 |
| `NEW_MULTITURN_SECOND_RUN_20261001.json` | 新Schema第二轮2/5；3题SQL值正确但统一路由回退 |
| `NEW_MULTITURN_ROUTER_REPLAY_20261001.json` | 路由补丁后新Schema严格5/5；11API均completed、25030 tokens；已曝光自建开发五轮，非盲测，不替代全44题38/44 |
| `MODEL_HTTP_SCALE_FIRST_RUN_20261001.json` | 三种行规模、并发1/2/4真实模型HTTP24/24；小样本、token仅可见下界 |

各结果分别对应报告的输入、源码和时点；不把分批成绩拼成当前全量准确率。程序包使用 `--program-only --with-public-assets`，外置官方数据由冻结manifest恢复；空目录HTTP验收见ZIP旁 `.smoke.json`。完整本地迁移备份包含运行配置，仅作本机恢复，不能当公开程序包。

## 历史索引

本目录的 `ICT_TRACK8_*`、`NL2SQL_*` 历史报告随源码整体迁移，用于继承思路、解释旧实验和实现边界；其中 D 盘路径、8014 生产联调和历史成绩不表示独立项目已完成这些验收。

本项目的当前事实以以下文件和真实运行结果为准：

- `ACCEPTANCE.md`：全部赛题要求与未完成项。
- `BASELINE_MANIFEST.json`：迁移时逐文件校验与原始版本。
- `SAMPLE_INGEST_REPORT.json`：实际文件、OCR 和切片入库记录。
- `INDEPENDENT_ACCEPTANCE_REPORT.json`：本地合成样本的问数、文档及跨源验收结果，不能当作公开基准成绩。
- `HYBRID_ACCEPTANCE_REPORT.json`：实际BGE与BM25/RRF的当前验收。
- `CHINOOK_RULES_REPORT.json`、`SCHEMA_SCALE_REPORT.json`：公开数据库开发题与合成干扰表。
- `ROBUSTNESS_REPORT.json`：真实OCR的原图/增强成对结果。
- `REAL_MODEL_FIRST_RUN_20261001.json`、`REAL_MODEL_REPORT.json`：指定gpt-6-luna真实44题首轮11/44、第二轮32/44；当前预检HTTP200。第二轮83API均completed，上报443785 tokens；SQL值19/19但整题16/19，文档8/13，4篇摘录回退不计生成通过。结果包含服务端规范化，不是裸模型或官方准确率。
- `REAL_MODEL_SECOND_RUN_20261001.json`：第二轮完整报告的字节一致归档，保留修复前失败证据。
- `REAL_MODEL_TARGETED_REPORT.json`：独立定向15/19，所选12失败9个通过、7前置6个通过；38API均completed，上报195833 tokens。仍有qa-07/qa-08摘录回退、政策route错误与预测标签引用不稳4项失败；未重跑全44，不能拼接当前全量准确率。
- `HTTP_MODEL_ACCEPTANCE_REPORT.json`：实际HTTP2/2，SSE查询华东销售额29584及保修真实生成引用；网页人工三源预测33134.08另已验证，均为局部接口/交互证据。
- `MODEL_API_PROBE.json`：从第二轮保存的真实预检派生为HTTP200，不额外调用API；早期401原始证据在`MODEL_API_PROBE_INITIAL_20261001.json`，`MODEL_VALIDATION_UPDATE_20261001.md`仅为准备阶段历史。
- `TEXT_QUALITY_FIRST_RUN.json`、`TEXT_QUALITY_REPORT.json`：繁简、有限错字及原文保留开发对照，首次13/14，修复扩展15/15。
- `LIVE_SQL_FIRST_RUN.json`、`LIVE_SQL_REPORT.json`：实际WAL更新、日期格式、业务别名、URI和事务释放，首次1/5→修复5/5。
- `FUSION_CONSISTENCY_FIRST_RUN.json`、`FUSION_CONSISTENCY_REPORT.json`：实际任务中途写库、篡改、移走及重入库，首次1/5→修复5/5。
- `SOURCE_CONSISTENCY_UPDATE_20261001.md`：单查询/跨SQL读取快照与文档版本复核的实现和代价。
- `SERVICE_CONSISTENCY_REPORT.json`：新版8030实际HTTP四步跨源，客单价29584/3、SQL同快照和文档版本4项核对。
- `ORIENTATION_FIRST_RUN.json`、`ORIENTATION_REPORT.json`、`ORIENTATION_UPDATE_20261001.md`：无EXIF方向/倾斜告警等新增契约首次0/10，修复并扩展图片/PDF/Word/预览13/13；不是OCR文字准确率从0提升。
- `DOMAIN_TRANSFER_FIRST_RUN.json`、`DOMAIN_TRANSFER_REPORT.json`：新差旅Schema首轮6/8与修复后8/8，明确为自编审计/开发回归。
- `HELDOUT_FINANCE_FIRST_RUN.json`、`HELDOUT_FINANCE_REPORT.json`：协作代理订阅领域初测0/12；修复后9/11有效题，12总尝试，HF11金标并列口径无效排除。字段和数值均核对，冻结输入在benchmarks/heldout_finance；后测为已曝光开发回归，非官方或严格独立盲测。
- `LOCAL_REGRESSION_REPORT.json`：最终完整本地706 passed、0 failed、1条Starlette弃用告警；新日期/主体覆盖及既有门控专项170项通过。
- `BROWSER_MODEL_ACCEPTANCE_20261001.json`：实际DOM两例，SQL29584和带PDF公式/Excel参数/数据库证据的预测33134.08。定向API评测早于最终文档问题保护补丁；最终补丁经真实store本地回归，未再调用API。
- `../ict-track8/requirements-tested.txt`、`../ict-track8/ENVIRONMENT.json`：17项直接依赖现场版本与精简平台记录，非完整传递依赖lock、跨平台wheel锁或安装来源证明。
- `RELIABILITY_UPDATE_20261001.md`：规划上下文、聚合覆盖与原文件行号修复，以及本轮回归口径。
- `history/REAL_MODEL_REPORT_PRE_LUNA.json`：旧模型历史实验，不是gpt-6-luna验收。

按用户要求本轮只更新程序，不制作参赛PPT/Word/PDF。delivery及其SOURCE_MANIFEST是本轮文字质量更新前的历史快照，程序包排除此目录。真实模型已有实际结果但尚未全题通过，严格独立未知题验收仍需完成。程序包新目录验收见压缩包旁.smoke.json；历史方向包29/29，本轮最终包需核对旁报告。

评测支持`--workers 3`并行独立会话；`--case-ids`选择指定题并自动补齐同会话前置轮；`--output docs/新报告.json`保持报告独立且不覆盖历史。追加`--preview`检查题序、范围与输出路径，不读取密钥、不联网。完整44题与定向题报告各自解释，不将分批通过拼成全量通过。
