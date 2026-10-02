# OmniAgent · ICT 赛题八独立项目

本项目针对《中国电子杯》第三届高校 ICT 产教融合创新大赛赛题八：多模态数据驱动的可解释精准问数/问答智能体。

这是 D 盘独立实现，当前根目录为 `D:\ICT8-OmniAgent`。原睿视清源生产项目不被修改，也不是本项目运行依赖。已有 NL2SQL 连同最新未提交的优化整体迁移；适合题目的文档解析、证据定位与安全执行组件保留，后续能力在此仓库实现。

2026-10-01 从 E 盘完整迁移了 1,407 个文件（534,638,727 字节），逐文件 SHA-256、Git HEAD 和未提交状态均一致。旧 E 盘目录保留为迁移快照；历史报告的旧路径不改写。官方大型数据位于 `D:\ICT8-OfficialDatasets`，历史交付包和迁移校验位于 `D:\ICT8-Backups`。

## 当前固定源码验收（2026-10-02，第六轮）

| 范围 | 上轮/隔离基线 → 当前 | 边界 |
|---|---|---|
| 综合真实模型开发题 | 41/44 → 43/44 | 已曝光合成开发题，含1项路由失败 |
| Sakila跨Schema自编56轮 | 14/56 → 15/56 | 独立题3/36、会话turn12/20、整段0/4；非官方榜单 |
| OHR-Bench固定原件36题 | EM7/36持平；F1 .317567 → .327826 | 15原件资源偏置子集重复评测，F1不是语义准确率 |
| 显式完整结果开发验证 | 4/6 → 6/6 | `complete_results=true`；两个结果各19119行/192页，全cells核对，API0 |
| 完整本地回归 | 2858 passed、0 failed、2 skipped、1 warning、12 subtests | 工程合同，不代表模型准确率 |

三组真实API只使用`gpt-6-luna / medium`：348调用、340完成、8失败、0丢失；所有失败、退步、未知usage和固定分母保留。82个backend文件的最终源码一致性审计通过，见 [第六轮说明](docs/OPTIMIZATION_ROUND6_20261002.md) 和 [封存报告](docs/ROUND6_FINAL_SOURCE_AUDIT_20261002.json)。四项顶级效果与泛化目标仍进行中，优先改善跨Schema语义和原件完整答案；下方旧轮数值只对应其历史版本。

## 目录

| 路径 | 内容 |
|---|---|
| `ict-track8/backend` | NL2SQL、文档分析、智能体、独立知识库服务 |
| `ict-track8/frontend` | 真实 API 演示界面 |
| `ict-track8/tests` | 功能、安全、语义与接口测试 |
| `ict-track8/eval` | 可执行金标和规模评测 |
| `ict-track8/data` | 可重建小型数据库输入、指标口径和旧评测记录 |
| `specification` | 官方赛题 PDF 及逐页文本 |
| `samples` | 实际多格式示例文件，明确标注为合成样本 |
| `docs` | 需求验收、架构、原始迁移哈希清单与技术说明 |
| `tools` | 独立迁移、启动、评测与交付工具 |
| `delivery` | 历史参赛材料快照；本轮仅更新程序，程序包不包含此目录 |
| `backups` | 已校验基线 ZIP，仅本地保留 |

## 快速启动

```powershell
cd D:\ICT8-OmniAgent
python -m pip install -r ict-track8/requirements.txt
python tools/run_server.py --port 8030
```

浏览器打开 http://127.0.0.1:8030。API 和前端使用同一端口，不占用原项目的 8014、8020、8021。

### 真实模型

本轮只使用 `gpt-6-luna` 和 `https://spacetimeai.cc/v1`。固定配置已保存在 `runtime/model_config.json`，下次无需重新输入；该文件不进入 Git、ZIP 或浏览器。

```powershell
python tools/probe_model.py
python tools/evaluate_model.py --full --preview
python tools/evaluate_model.py --full
python tools/evaluate_model.py --full --workers 3
python tools/evaluate_model.py --workers 3 --case-ids cross-turn-2 sql-turn-5 --output docs/REAL_MODEL_SELECTED_REPLAY.json --preview
python tools/evaluate_model.py --workers 3 --case-ids cross-turn-2 sql-turn-5 --output docs/REAL_MODEL_SELECTED_REPLAY.json
python tools/run_server.py --with-model --port 8030
```

2026-10-01第三轮历史预检HTTP200。该轮44题端到端合成开发验收见 `docs/REAL_MODEL_THIRD_RUN_20261001.json`：**38/44**，文档12/13、SQL17/19、公式3/3、预测3/3、文档→SQL1/1、SQL→文档0/1、阈值0/2、政策1/1、澄清1/1。87次API均completed，网关上报463367 tokens（输入447071、输出16296）。成绩包含模型路由、服务端计划规范化和安全门；不是裸模型准确率、官方成绩或独立盲测。调用成功不等于答案正确，未提供第三方单价，不估费用。

历史首轮11/44、第二轮32/44和定向15/19分别保留在 `docs/REAL_MODEL_FIRST_RUN_20261001.json`、`docs/REAL_MODEL_SECOND_RUN_20261001.json`、`docs/REAL_MODEL_TARGETED_REPORT.json`，不合并分批成绩。第三轮实测早于最新Schema限定、局部计数、单位契约与单源路由补丁；这些补丁不能据此宣称已提高当前全量真实成绩。

新增五轮业务Schema在路由补丁后严格真实回放 **5/5**，见 `docs/NEW_MULTITURN_ROUTER_REPLAY_20261001.json`：11次API全部completed、0失败/0丢失，25030 tokens，查询总耗时152.572秒；原始SQL、来源、无旧过滤继承与退款公式600均核对通过。首轮0/5、第二轮2/5原样保留，输入及gold不改。这个已曝光自建开发五轮证明该代表性闭环，不是未知盲测，不替代全44题38/44。Chinook第二轮严格模型12/12，但仅10个非空开发题，另外2个为空结果边界，详见下方数据验收。

真实HTTP验收另见`docs/HTTP_MODEL_ACCEPTANCE_REPORT.json`，2/2通过：SSE查询2025年华东销售额29584，标准硬件保修回答有真实生成及引用；网页人工操作三源预测实际返回33134.08。它们是局部接口/交互证据，不替代完整题集或吞吐验收。

单源SQL/document默认保留用户原问题，SQL只有经服务端验证的上下文合并才继承，防止同类跨主题问题被模型自由改写为另一主题。补丁时序与真实题集证据分别记录，不把本地回归当作最新真实模型复测。两例实际DOM/结果证据见 `docs/BROWSER_MODEL_ACCEPTANCE_20261001.json`。

`--preview`不读取凭据或联网；`--full`内置单次预检，401/403停止后续题。44题覆盖问数、生成问答、五类跨源、连续五轮及澄清回填，SQL内部也接真实模型。默认演示使用规则和原文摘录，不把回退当作模型通过。网关返回的model字段核对只验证服务端声明，不能证明其底层模型身份。

`--workers 3`只并行独立会话，同一会话仍依次执行。`--case-ids`可用空格或逗号分隔；选择某一多轮题会自动纳入该会话全部前置轮，保留原始题序与历史计数，未知ID在读取凭据前拒绝。`--output`指定项目docs内的新JSON报告，拒绝覆盖已存在历史文件。定向报告显式标记所选题与前置题，不代替全量报告。

真实模型HTTP规模采样见 `docs/MODEL_HTTP_SCALE_FIRST_RUN_20261001.json`：1千/1万/10万行、并发1/2/4，共24/24请求通过，并采样服务器RSS。它只测合成单表索引聚合的真实路由与SQL规划，结果解释由本地序列化，没有额外模型答案生成；未测文档页数/OCR规模，usage为可见下界，不代表最大吞吐或全量模型能力。

### 可重现评测与打包

```powershell
python tools/create_sample_corpus.py --ingest
python tools/evaluate_independent.py --dense
python tools/evaluate_chinook.py
python tools/evaluate_chinook.py --model --output docs/CHINOOK_MODEL_NEW_REPLAY.json
python tools/fetch_adventureworks.py
python tools/evaluate_adventureworks.py --output docs/ADVENTUREWORKS_NEW_REPLAY.json
python tools/fetch_ohr_bench.py --restore-frozen
python tools/evaluate_ohr_bench.py --output docs/OHR_BENCH_LOCAL_NEW_REPLAY.json
python tools/evaluate_ohr_bench.py --with-model --output docs/OHR_BENCH_MODEL_NEW_REPLAY.json
python tools/evaluate_schema_scale.py
python tools/evaluate_domain_transfer.py
python tools/evaluate_heldout_finance.py --output docs/HELDOUT_FINANCE_REPLAY.json
python tools/evaluate_pdf_outline.py
python tools/evaluate_robustness.py
python tools/evaluate_text_quality.py
python tools/evaluate_orientation.py
python tools/evaluate_live_sql.py
python tools/evaluate_fusion_consistency.py
python tools/evaluate_rag_scale.py
python ict-track8/scripts/package_delivery.py --program-only --with-public-assets --output dist/ict8-complete.zip
python ict-track8/scripts/verify_package.py dist/ict8-complete.zip
```

Dense首次缺失时运行 `python tools/fetch_public_assets.py`。完整资产包包含公开模型权重、Chinook及许可；源码包可省略 `--with-public-assets` 并根据公开下载清单恢复资产。依赖仍需按requirements安装，真实模型密钥单独配置。开发题成绩不代表官方未知题准确率。

官方原始大型资产放在 `D:\ICT8-OfficialDatasets`，不进入程序ZIP。恢复及口径见 [官方数据工程验收](docs/OFFICIAL_DATASET_ACCEPTANCE.md)：Chinook第二轮严格模型12/12（10非空+2空结果边界）；微软官方AdventureWorks CSV三表SQLite移植4/6，另2题19119组被100行上限截断；OHR-Bench冻结12原题/7原PDF的资源受限pilot只有1个实质回答、9个拒答、2个摘录回退，EM为0/12。不能把已接入数据等同于效果达标。OHR现有manifest须用 `--restore-frozen` 恢复，禁止重新选题冒充同一实验；原PDF研究用途及版权条件保留。

`ict-track8/requirements-tested.txt`记录现场metadata核对的17项直接依赖版本；`ict-track8/ENVIRONMENT.json`记录CPython3.12.10、Windows11及复现边界，两者均包含在程序包。这不是完整传递依赖lock、跨平台wheel锁或离线安装包，也未证明安装来源或全新虚拟环境可复现。默认OCR为RapidOCR；可选Tesseract依赖移至`ict-track8/requirements-tesseract.txt`，还需系统二进制与语言数据，当前未安装或验收。

本轮只更新程序，不制作PPT/Word/PDF；历史材料仍在本地delivery，程序包通过--program-only排除。运行安装以ict-track8/requirements.txt为准。
已完成的最新完整本地回归为 **860 passed、0 failed、1条Starlette弃用告警，39.78秒（报告脚本总耗时41.656秒）**，见 `docs/LOCAL_REGRESSION_REPORT.json`；包含最新单源路由补丁，完整本地回归已通过。这是本地功能契约成绩，不是模型准确率。第五类完成带来源事实与Excel阈值的实际比较。OpenCC繁简检索、文字质量告警和确认校正预览的15组开发审计通过；有限错字词表，不是通用中文纠错。实际SQLite更新与跨源中途变化审计各5/5：WAL更新刷新缓存，多步SQL固定同一读取快照，文档版本变化停止计算。无EXIF扫描件方向/倾斜、正向文字顺序和网页校正预览的图片/PDF/Word等13项开发审计通过；方向证据不足不猜测，原文件保留。

程序包新目录验收见压缩包旁`.smoke.json`；历史方向包29/29，本轮最终包需核对旁报告。上述本地回归不是全新依赖环境安装、最终ZIP解包或真实模型全量通过的证明。

新增订阅账单Schema审计首轮0/12；修复通用中文日期识别和真实选定表名覆盖后9/11有效题通过，12题总尝试，HF11因金标并列排序口径错误排除可靠正确率分母。字段与数值均核对，初测保留；后测属于已曝光题的开发回归，不是官方或严格独立盲测。未知主题词继续澄清，数值时间戳过滤可用，但月/年日历分组明确澄清，避免NULL分组静默错误。第三轮44题仍有6个整题失败；新Schema路由补丁后5/5只属于该开发回放，未知任务泛化、最新补丁后的全量44题真实成绩和最大端到端模型吞吐仍需验收。

```powershell
cd ict-track8
python -m pytest tests -q
```

已有模块在迁移后通过 333 个测试。该结果是迁移基线回归，不能证明全部赛题完成、实际 OCR 已启用或泛化准确率达到比赛门槛。最新状态见 `docs/ACCEPTANCE.md`。

## 基线保护

`backups/baseline-20261001.zip` 保存迁移时的 174 个文件（包含未提交的 NL2SQL 与前端改动）。`docs/BASELINE_MANIFEST.json` 记录来源提交、每个文件 SHA-256、大小和排除项；迁移后逐文件与原目录、ZIP 内容核对一致。

运行默认使用本项目的演示数据库；正式数据必须显式配置。本项目不读取原项目密钥、不复用其手册库、不要求原 RAG 服务在线。
