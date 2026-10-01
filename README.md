# OmniAgent · ICT 赛题八独立项目

本项目针对《中国电子杯》第三届高校 ICT 产教融合创新大赛赛题八：多模态数据驱动的可解释精准问数/问答智能体。

这是 E 盘独立实现。原睿视清源生产项目不被修改，也不是本项目运行依赖。已有 NL2SQL 连同最新未提交的优化整体迁移；适合题目的文档解析、证据定位与安全执行组件保留，后续能力在此仓库实现。

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
cd E:\ICT8-OmniAgent
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

当前真实预检HTTP200，44题端到端开发验收首轮11/44、第二轮32/44，分别见 `docs/REAL_MODEL_FIRST_RUN_20261001.json` 和 `docs/REAL_MODEL_REPORT.json`。第二轮包含服务端计划规范化与安全门，并非裸模型准确率或官方/独立盲测成绩。SQL执行值19/19匹配，但要求模型路由等完整契约后仅16/19整题通过；13个文档题通过8个，4个摘录回退不计生成通过。83次API调用均completed，网关上报443785 tokens；API成功不等于答案正确，未提供第三方单价，不估费用。

后续修复进行了独立定向验收：`docs/REAL_MODEL_TARGETED_REPORT.json`为15/19，其中第二轮所选12个失败项9个通过，自动补入的7个前置轮6个通过。38次API调用均completed，上报195833 tokens；3路并行约3分15秒。仍有4项未过：qa-07/qa-08自由改写无法通过有界支持守卫，保留原文摘录；政策版本比较仍选文档问答路线；预测追问的SQL展示标签与下游引用不一致。本轮未重跑全44题，不能将分批结果拼成当前全量准确率或44/44。第二轮完整证据另原样保存在`docs/REAL_MODEL_SECOND_RUN_20261001.json`。

真实HTTP验收另见`docs/HTTP_MODEL_ACCEPTANCE_REPORT.json`，2/2通过：SSE查询2025年华东销售额29584，标准硬件保修回答有真实生成及引用；网页人工操作三源预测实际返回33134.08。它们是局部接口/交互证据，不替代完整题集或吞吐验收。

定向真实评测早于最终文档问题保护补丁：单源SQL/document默认保留用户原问题，SQL只有经服务端验证的上下文合并才继承，防止同类跨主题问题被模型自由改写为另一主题。该最终补丁已通过真实KnowledgeStore本地回归，没有再调用API；定向分数不表示最终补丁已重新跑真实题集。两例实际DOM/结果证据见`docs/BROWSER_MODEL_ACCEPTANCE_20261001.json`。

`--preview`不读取凭据或联网；`--full`内置单次预检，401/403停止后续题。44题覆盖问数、生成问答、五类跨源、连续五轮及澄清回填，SQL内部也接真实模型。默认演示使用规则和原文摘录，不把回退当作模型通过。网关返回的model字段核对只验证服务端声明，不能证明其底层模型身份。

`--workers 3`只并行独立会话，同一会话仍依次执行。`--case-ids`可用空格或逗号分隔；选择某一多轮题会自动纳入该会话全部前置轮，保留原始题序与历史计数，未知ID在读取凭据前拒绝。`--output`指定项目docs内的新JSON报告，拒绝覆盖已存在历史文件。定向报告显式标记所选题与前置题，不代替全量报告。

### 可重现评测与打包

```powershell
python tools/create_sample_corpus.py --ingest
python tools/evaluate_independent.py --dense
python tools/evaluate_chinook.py
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

`ict-track8/requirements-tested.txt`记录现场metadata核对的17项直接依赖版本；`ict-track8/ENVIRONMENT.json`记录CPython3.12.10、Windows11及复现边界，两者均包含在程序包。这不是完整传递依赖lock、跨平台wheel锁或离线安装包，也未证明安装来源或全新虚拟环境可复现。默认OCR为RapidOCR；可选Tesseract依赖移至`ict-track8/requirements-tesseract.txt`，还需系统二进制与语言数据，当前未安装或验收。

本轮只更新程序，不制作PPT/Word/PDF；历史材料仍在本地delivery，程序包通过--program-only排除。运行安装以ict-track8/requirements.txt为准。
最终版本完整本地回归706 passed、0 failed、1条Starlette弃用告警；中文日期角色、Schema主体覆盖及既有模型门控专项170项通过。第五类完成带来源事实与Excel阈值的实际比较。OpenCC繁简检索、文字质量告警和确认校正预览的15组开发审计通过；有限错字词表，不是通用中文纠错。实际SQLite更新与跨源中途变化审计各5/5：WAL更新刷新缓存，多步SQL固定同一读取快照，文档版本变化停止计算。无EXIF扫描件方向/倾斜、正向文字顺序和网页校正预览的图片/PDF/Word等13项开发审计通过；方向证据不足不猜测，原文件保留。

程序包新目录验收见压缩包旁`.smoke.json`；历史方向包29/29，本轮最终包需核对旁报告。上述本地回归不是全新依赖环境安装、最终ZIP解包或真实模型全量通过的证明。

新增订阅账单Schema审计首轮0/12；修复通用中文日期识别和真实选定表名覆盖后9/11有效题通过，12题总尝试，HF11因金标并列排序口径错误排除可靠正确率分母。字段与数值均核对，初测保留；后测属于已曝光题的开发回归，不是官方或严格独立盲测。未知主题词继续澄清，数值时间戳过滤可用，但月/年日历分组明确澄清，避免NULL分组静默错误。真实模型第二轮12项失败后进行了上述定向修复，最新定向仍有4项失败；未知任务泛化、当前全量真实准确率与真实模型吞吐待继续验收。

```powershell
cd ict-track8
python -m pytest tests -q
```

已有模块在迁移后通过 333 个测试。该结果是迁移基线回归，不能证明全部赛题完成、实际 OCR 已启用或泛化准确率达到比赛门槛。最新状态见 `docs/ACCEPTANCE.md`。

## 基线保护

`backups/baseline-20261001.zip` 保存迁移时的 174 个文件（包含未提交的 NL2SQL 与前端改动）。`docs/BASELINE_MANIFEST.json` 记录来源提交、每个文件 SHA-256、大小和排除项；迁移后逐文件与原目录、ZIP 内容核对一致。

运行默认使用本项目的演示数据库；正式数据必须显式配置。本项目不读取原项目密钥、不复用其手册库、不要求原 RAG 服务在线。
