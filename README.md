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
python tools/run_server.py --with-model --port 8030
```

当前真实预检仍返回 HTTP 401，见 `docs/REAL_MODEL_REPORT.json`。`--preview`不读取凭据或联网；`--full`内置单次预检，401/403停止后续题。44题覆盖问数、生成问答、五类跨源、连续五轮及澄清回填，SQL内部也接真实模型。默认演示使用规则和原文摘录，不把回退当作模型通过。

### 可重现评测与打包

```powershell
python tools/create_sample_corpus.py --ingest
python tools/evaluate_independent.py --dense
python tools/evaluate_chinook.py
python tools/evaluate_schema_scale.py
python tools/evaluate_domain_transfer.py
python tools/evaluate_pdf_outline.py
python tools/evaluate_robustness.py
python tools/evaluate_text_quality.py
python tools/evaluate_live_sql.py
python tools/evaluate_fusion_consistency.py
python tools/evaluate_rag_scale.py
python ict-track8/scripts/package_delivery.py --program-only --with-public-assets --output dist/ict8-complete.zip
python ict-track8/scripts/verify_package.py dist/ict8-complete.zip
```

Dense首次缺失时运行 `python tools/fetch_public_assets.py`。完整资产包包含公开模型权重、Chinook及许可；源码包可省略 `--with-public-assets` 并根据公开下载清单恢复资产。依赖仍需按requirements安装，真实模型密钥单独配置。开发题成绩不代表官方未知题准确率。

`delivery/requirements-tested.txt`固定当前实测的直接依赖版本；`delivery/ENVIRONMENT.json`另记录全部已安装包、Python及平台。它是实测环境记录，不是跨平台wheel锁或离线安装包。默认OCR为RapidOCR；可选Tesseract未在当前环境安装或验收。

本轮只更新程序，不制作PPT/Word/PDF；历史材料仍在本地delivery，程序包通过--program-only排除。运行安装以ict-track8/requirements.txt为准。
最新完整本地回归549项通过；第五类完成带来源事实与Excel阈值的实际比较。OpenCC繁简检索、文字质量告警和确认校正预览的15组开发审计通过；有限错字词表，不是通用中文纠错。新增实际SQLite更新与跨源中途变化审计各5/5：WAL更新刷新缓存，多步SQL固定同一读取快照，文档版本变化停止计算。真实模型401与未知任务仍待验收，不把stub算模型成绩。

```powershell
cd ict-track8
python -m pytest tests -q
```

已有模块在迁移后通过 333 个测试。该结果是迁移基线回归，不能证明全部赛题完成、实际 OCR 已启用或泛化准确率达到比赛门槛。最新状态见 `docs/ACCEPTANCE.md`。

## 基线保护

`backups/baseline-20261001.zip` 保存迁移时的 174 个文件（包含未提交的 NL2SQL 与前端改动）。`docs/BASELINE_MANIFEST.json` 记录来源提交、每个文件 SHA-256、大小和排除项；迁移后逐文件与原目录、ZIP 内容核对一致。

运行默认使用本项目的演示数据库；正式数据必须显式配置。本项目不读取原项目密钥、不复用其手册库、不要求原 RAG 服务在线。
