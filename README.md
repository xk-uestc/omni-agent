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
| `delivery` | 当前Word/PDF报告、匿名答辩PPT、架构图、实测依赖与证据清单 |
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
python tools/evaluate_model.py --full
python tools/run_server.py --with-model --port 8030
```

当前真实探测返回 HTTP 401，见 `docs/MODEL_API_PROBE.json`。有效凭据就绪前默认演示使用规则规划和原文摘录，不把回退当作真实模型验收。

### 可重现评测与打包

```powershell
python tools/create_sample_corpus.py --ingest
python tools/evaluate_independent.py --dense
python tools/evaluate_chinook.py
python tools/evaluate_schema_scale.py
python tools/evaluate_domain_transfer.py
python tools/evaluate_pdf_outline.py
python tools/evaluate_robustness.py
python tools/evaluate_rag_scale.py
python ict-track8/scripts/package_delivery.py --with-public-assets --output dist/ict8-complete.zip
python ict-track8/scripts/verify_package.py dist/ict8-complete.zip
```

Dense首次缺失时运行 `python tools/fetch_public_assets.py`。完整资产包包含公开模型权重、Chinook及许可；源码包可省略 `--with-public-assets` 并根据公开下载清单恢复资产。依赖仍需按requirements安装，真实模型密钥单独配置。开发题成绩不代表官方未知题准确率。

`delivery/requirements-tested.txt`固定当前实测的直接依赖版本；`delivery/ENVIRONMENT.json`另记录全部已安装包、Python及平台。它是实测环境记录，不是跨平台wheel锁或离线安装包。默认OCR为RapidOCR；可选Tesseract未在当前环境安装或验收。

正式材料见`delivery/README.md`。报告区分当前开发验收、模型鉴权失败和待执行实验；决赛材料为准备稿，未套用尚未提供的组委会模板。
最新本地回归455项通过；差旅新Schema自编审计首次6/8、修复后8/8；实际PDF目录金标首次2/8、修复后8/8，原始失败与冻结输入均保留。统一澄清支持真实指标/角色选项、时间输入、趋势粒度、时间顺序与刷新后会话继承，见`docs/OUTLINE_CLARIFICATION_UPDATE_20261001.md`。规划上下文测试为捕获stub，不当作真实模型效果。前轮修复见`docs/RELIABILITY_UPDATE_20261001.md`；正式材料仍为ade1b60快照，最终定稿需更新。

```powershell
cd ict-track8
python -m pytest tests -q
```

已有模块在迁移后通过 333 个测试。该结果是迁移基线回归，不能证明全部赛题完成、实际 OCR 已启用或泛化准确率达到比赛门槛。最新状态见 `docs/ACCEPTANCE.md`。

## 基线保护

`backups/baseline-20261001.zip` 保存迁移时的 174 个文件（包含未提交的 NL2SQL 与前端改动）。`docs/BASELINE_MANIFEST.json` 记录来源提交、每个文件 SHA-256、大小和排除项；迁移后逐文件与原目录、ZIP 内容核对一致。

运行默认使用本项目的演示数据库；正式数据必须显式配置。本项目不读取原项目密钥、不复用其手册库、不要求原 RAG 服务在线。
