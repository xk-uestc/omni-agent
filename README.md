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
| `backups` | 已校验基线 ZIP，仅本地保留 |

## 快速启动

```powershell
cd E:\ICT8-OmniAgent
python -m pip install -r ict-track8/requirements.txt
python tools/run_server.py --port 8030
```

浏览器打开 http://127.0.0.1:8030。API 和前端使用同一端口，不占用原项目的 8014、8020、8021。

```powershell
cd ict-track8
python -m pytest tests -q
```

已有模块在迁移后通过 333 个测试。该结果是迁移基线回归，不能证明全部赛题完成、实际 OCR 已启用或泛化准确率达到比赛门槛。最新状态见 `docs/ACCEPTANCE.md`。

## 基线保护

`backups/baseline-20261001.zip` 保存迁移时的 174 个文件（包含未提交的 NL2SQL 与前端改动）。`docs/BASELINE_MANIFEST.json` 记录来源提交、每个文件 SHA-256、大小和排除项；迁移后逐文件与原目录、ZIP 内容核对一致。

运行默认使用本项目的演示数据库；正式数据必须显式配置。本项目不读取原项目密钥、不复用其手册库、不要求原 RAG 服务在线。
