# Omni-Agent 本地启动与五个展示流程 — 2026-10-13

本指南面向首次拿到仓库的开发者和比赛演示人员。产品改造起点为 `memory` 上的 `19714677ed01fb09e6a1d75a49a85f37b1520c06`；交付代码 SHA 为 `b16ed669cc86c85799d072fa55ec8df22a33ada8`；最终归档提交可用 `git log -1` 查看。功能状态按真实回执区分，未完成的任务保留原状，详见 [体验报告](PRODUCT_EXPERIENCE_20261013.md)。

## 启动完整系统

以下命令在 Linux 仓库根目录执行。先检查工作区，保留已有修改；不要用强制重置覆盖他人的工作。

```bash
git status
git branch --show-current
git log -1
```

使用交付的 `memory` 版本。已有兼容 `.venv` 时继续使用；新环境按本次保存的原生 PDF/BM25/CPU OCR 依赖创建独立环境：

```bash
uv venv .venv --python /usr/bin/python3
uv pip install --python .venv/bin/python -r docs/foundation/local_run_20261012/requirements-installed.txt
uv pip check --python .venv/bin/python
.venv/bin/python docs/foundation/local_run_20261012/prepare.py --fetch-native-pdf
```

没有 `uv` 时，可使用 `python3 -m venv .venv` 与 `.venv/bin/python -m pip install -r docs/foundation/local_run_20261012/requirements-installed.txt`。已经有虚拟环境时不要为了照抄命令重新创建它。

`prepare.py` 在 `runtime/local_run_20261012/` 创建隔离数据库与配置，复制启动资源，下载并校验既有单页教材。原件已有本地副本时，也可传 `--native-pdf /绝对路径/原件.pdf`，同样必须通过固定 SHA 校验。该脚本包含明确标识的人工公式集成映射，不修改生产业务表或 Gold。

先用默认无付费模型模式启动，避免资料准备过程顺带消耗模型额度：

```bash
OMNI_MODEL_ENABLED=0 runtime/local_run_20261012/launch.sh >> runtime/local_run_20261012/backend.log 2>&1
```

它从 `.venv` 启动实际 FastAPI `backend.app`，通过部署 ASGI 入口挂载原项目静态前端，监听 `0.0.0.0:8030`。服务在当前终端持续运行；另开终端执行：

```bash
curl --noproxy '*' http://127.0.0.1:8030/health
curl --noproxy '*' http://127.0.0.1:8030/api/v1/documents/ocr/health
.venv/bin/python runtime/local_run_20261012/ingest_samples.py
.venv/bin/python runtime/local_run_20261012/verify_http.py
```

`ingest_samples.py` 上传仓库原有演示资料，记录原字节 SHA；`verify_http.py` 上传 `local-warranty` 与 `local-labor`，并验证真实 HTTP/SSE。**该旧验证脚本应仅在上述无模型服务下运行**，其报告标记为无付费模式，不用于新版本真实模型验收。

再下载并入库少量官方 PDF：

```bash
.venv/bin/python tools/download_product_pdfs.py
.venv/bin/python tools/download_product_pdfs.py --ingest --api-base http://127.0.0.1:8030
```

第一条只下载/校验；第二条提交真实入库请求，不调用语言模型。全部四份入库时，上海加密 PDF 会返回 HTTP 400，脚本记录拒收并继续处理其他文件，最终退出码为 1。只体验无加密文件可选：

```bash
.venv/bin/python tools/download_product_pdfs.py --document-id public-stats-disclosure-2024 --ingest
```

来源、SHA、页数与许可边界见 [公开 PDF 清单](PUBLIC_PDFS_20261013.json)。下载脚本限制四份清单、单文件 20 MiB、1000 页及下载时限；原件不随 Git 发布。下载回执与入库回执分别保存在输出目录，不相互覆盖。

## 启用已获授权的生成与规划

先停止自己刚启动的前台服务，再使用私有配置启动。不要同时启动两个占用 8030 的进程。

```bash
OMNI_MODEL_ENABLED=1 \
OMNI_MODEL_CONFIG=/绝对路径/私有模型配置.env \
runtime/local_run_20261012/launch.sh >> runtime/local_run_20261012/backend.log 2>&1
```

私有文件需要 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_API_KEY`，权限为 `0600`；当前接口基址为 `https://api.deepseek.com`，本轮模型为 `deepseek-flash`。请在本机配置密钥，不放进仓库或聊天。当前部署资源包含这次用户授权的累计 **40 次/¥10** 保护；新部署的付费使用仍需自己的账户与明确预算授权。

重新检查 `/health`：生成模式应为 `responses`、模型为实际配置值。本轮有可用 A100 GPU，但部署采用 CPU RapidOCR、BM25 与远程生成，没有启动本地训练或本地生成权重。

模型调用账本在 `runtime/local_run_20261012/interactive-model-ledger.json`，保留之前的调用；一条复杂任务可包含多次模型请求。不要删除账本“恢复额度”。默认无模型模式可用于文件解析、结构概览和原生表格验证，不能宣称由此完成了模型自主规划。

页面地址：

- 智能体工作台：[http://127.0.0.1:8030/](http://127.0.0.1:8030/)
- 资料工作台：[http://127.0.0.1:8030/knowledge.html](http://127.0.0.1:8030/knowledge.html)
- API 文档：[http://127.0.0.1:8030/docs](http://127.0.0.1:8030/docs)

前后端同源同端口，不需要额外前端服务器。局域网使用本机实际 IPv4 加 `:8030`；另一台设备的可达性取决于本机防火墙和网络，并非本地健康检查即可证明。

## 五个展示流程

演示时先让评委看到任务目标，再展示实际工具状态，最后打开一个来源或计算依据。不要口述页面上没有出现的工具步骤，也不要把折叠的“手动工具链”说成自主规划。

### Demo 1：上传 PDF → 文件概览 → 有出处的问答

资料条件：已上传国家统计局 2024 年政府信息公开年度报告，ID 为 `public-stats-disclosure-2024`，6 页。

1. 打开资料工作台，选择该报告；观察真实页数、索引片段与解析提示。
2. 点击“分析此文件”。它提交实际文档 ID，展示该文件的结构与原文概览；不要将结构概览宣传为模型通读后的专业结论。
3. 继续问：“2024年国家统计局召开了多少场国民经济运行情况新闻发布会？”
4. 展开引用并核对第一页原文。原文记录为 10 场；页面须有通过核验的回答才算此问答完成。

第一轮曾返回 `insufficient_evidence`，记录保留。数字与量词的原生 PDF 换行修复后，`final-http/real-pdf.json` 实际返回 `ok`、`model_grounded`、10 场及 4 条引用，约 6.081 秒。答案来自当次真实模型与来源校验，页面没有预写“10场”答案卡。另有 `final-http/overview.json` 验证 `local-warranty` 的免费结构概览与 4 条原文引用。

### Demo 2：原生 PDF 表格 → 单元格答案

资料条件：已通过固定 SHA 上传单页 `定额教材`，ID 为 `local-labor`。

问题：“高级熟练工的一般地区值是多少？”

展示文件选择、原生表格定位、实际答案 **10.26** 和原页引用。第一轮及 `final-http/table.json` 复验均为 `ok`、`answer_mode=native_table_literal`，1 条引用；新复验约 0.567 秒。没有明确单位时保留原文数值，不补造“元/工时”。这条单源路线不需要声称调用了 DependencyAgent 的 `document_cell` 工具。

### Demo 3：文档公式 → SQL 参数 → 安全计算

资料条件：已有 `经营指标公式`（`metric-definitions`）及演示销售库。切换为“全部资料”，不要限定为单文件问答。

问题：“根据文档《经营指标公式》的客单价公式，从数据库查询2025年华东销售额合计和订单数，再计算客单价。”

展示公式来源、数据库参数、时间/地区范围、计算表达式与单位。`final-http/formula.json` 实际为 `ok`、`fusion`、`model_validated`，真实执行 `document_formula → sql → calculate`：销售额 4,188,944.0，订单数 302，计算为 13,870.675496688742 `CNY*count^-1`，约 7.820 秒。参数语义、来源范围、目标公式和单位校验记录均保留。

首次问题增加了“去重订单数”，与该文档及指标目录的 `COUNT` 口径不一致，曾返回 `source_scope_unverified`。本次演示将问法明确改为文档的订单数口径，真实 SQL 使用 `COUNT(*)`；这是公开的口径调整，不是把去重要求悄悄忽略，不能宣称原 DISTINCT 问题已通过。

运输保险费可作为另一个明确条件的检查问题：

> 根据文档《定额教材》公式计算运输保险费；从数据库查询材料采购材料原价合计；从数据库查询保险参数材料运输保险费率合计。

该问题需要 `local-labor`、材料采购与保险参数映射。500 CNY 和 0.02 是上一轮人工集成数据，必须标明；2026-10-12 有 10.00 元的历史模型执行回执，本轮缩略问法“根据教材公式和参数计算运输保险费”重测仍返回 `source_scope_unverified`，没有计算成功；需要明确参数来源。

### Demo 4：SQL 排名 → 目标实体 → 文档检索

资料条件：演示销售数据库及 `champion-method` 销售经验文档。范围为“全部资料”。

问题：“先查询2025年销售额排名第一的地区，再检索该地区的销售经验”。

评委应能看到 SQL 查询的真实结果、该结果中的地区如何传给后续检索、经验出处与最后回答。不要提前指定获胜地区或画静态成功步骤。

第一轮因模型规划不可用而澄清。修复后的 `final-http/cross-source.json` 已为 `fusion`、`model_validated`，SQL 实际得到最高地区“东南”、销售额 6,280,960.41，然后使用该实体执行检索。因为资料只包含华东、华南、华北的经验，最终为 `insufficient_evidence`，明确拒绝把其他地区经验套给东南。这验证了真实规划、依赖传递与证据边界，完整经验答案仍未完成。

补充尝试过将华东、华南、华北限定放在句首或查询子句中的两种问法，实际均被 `sql_document_plan_scope_invalid` 拒绝。见 `additional-http/` 和 `scoped-http/`，这是当前规划稳定性的限制，不是成功演示。不得预先指定答案地区、把手写工具图当自主规划，或虚构缺少的经验。

原用户 B 的工种工时题仍缺语义匹配数据，本 Demo 使用已有销售场景，不将两道题混称为同题修复。

### Demo 5：模糊需求 → 主动澄清

范围为“全部资料”，输入：“帮我看看利润”。

第一轮与 `final-http/clarify.json` 新复验均真实询问利润口径、统计维度与时间范围；新复验约 4.568 秒，状态为 `clarification`，符合该澄清场景的预期。展示这是一项尚待条件补充的任务，而非费用审批或权限申请。用户可以补充“2025年，按地区查看毛利总额”；后续查询需要另一次实际验证，不能从澄清成功推断查询也成功。

若选中文件时又引用历史查询编号或另一份资料，系统应提示范围冲突。点击“取消文件限定，继续提问”只恢复输入，核对后再发送。

## 复验、记录与展示限制

新增真实服务验证入口为 `tools/verify_product_flows.py`。默认仅执行免费结构概览与原生表格；明确授权并保持模型账本时，才使用模型开关：

```bash
.venv/bin/python tools/verify_product_flows.py
.venv/bin/python tools/verify_product_flows.py --help
.venv/bin/python tools/verify_product_flows.py --with-model
```

可按该脚本 `--help` 的 `--case` 选项只复验指定任务，避免把每次 UI 调整都变成一轮付费问答。最终证据应包含请求、完整响应、SSE、源码 SHA、预算快照以及至少一条可回到原件的引用。

长文档原生表格批量入库受 40 页/30 秒预算约束，文字入库成功不代表全部表格可问数。加密文件必须先获得可读取版本，程序不会移除原件保护。扫描件 OCR 成功也不等于每个数字和单元格关系均已通过事实核验。

已完成的修复后回执索引为 `runtime/product_20261013/final-http/summary.json`，相邻目录保留各项 `.json` 与 `.sse`；6 项检查中 5 项达到验收断言，不限地区的跨源任务因真实资料缺失未完成完整答案。最终浏览器截图及额外失败已归档到体验报告；HTTP/SSE 完整内容保存为 `docs/product/evidence/http-and-sse.json.gz`。运行全套验收会因跨源资料不足返回非零退出码，这是如实记录，不表示六项全部失败。首轮失败仍保留，公式问题的 COUNT 口径调整也明确记录，不据演示场景宣布任意数据源和任意问题已经可用。

交付预算快照为累计35/40次、保守上界¥7.21897/¥10。验收后剩余次数用于用户体验；一条复杂提问可能消耗多次。达到上限后需新的预算授权，不清空账本。
