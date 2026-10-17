# 真实文档理解实现（2026-10-11，memory）

起点 fetch 后本地与 origin/memory 均为 `02741bced7d9d1ae0d14b7820cad2cc772aeba17`；仅未跟踪 QiMem，未修改。Linux 缺失 Windows 原件路径，已按用户授权恢复 OHR 原 PDF，按旧 SHA 校验；历史36题仅用于历史诊断。解析/检索算法冻结与远端实现为 `decb1ee1d98d186cf389413f6d0b1662ba43e46c`；之后仅增加计算审核重放安全检查，未修改解析器或检索算法。

新增评价协议及原件清单见 `benchmarks/real_document_20261011/README.md`。官方人工参考与原件位于 runtime，解析进程仅接收清单和原件。OHR 新12文档/34题；OmniDocBench v1.0 新24原始切片，12开发/12保留。父文档暴露审查发现 ODB 开发集一份报纸与历史 OHR 同父文档，已披露；保留集未发现历史或跨新集合父文档重叠。它们是有限研究子集，不是官方榜单。

## 生产接入

可选复杂 PDF 结构解析由 **MinerU 4.0.12** 提供；基础 ONNX 档本地 CPU 执行。没有调用远程解析或生成模型。保留 `DocumentChunker.parse_pdf()` 正文路径，再补充独立公式和有界表格行。

- `pdf_backend.LocalMinerUPdfBackend.parse()`：原件20MiB、默认40页/300秒，最多100页/600秒；页预算超限整体拒用，超时终止进程组。固定 CLI 参数，无 shell，不接收文档中的命令。拒绝暂不支持的旋转/偏移 CropBox，核对全部页数及坐标声明。
- `structured_document.normalize_mineru()`：统一段落/标题/阅读顺序/图像区域/表格单元格、行列跨度/公式结构。原件SHA、页码、display-point bbox、解析器版本、观察摘要和审核状态保留。单元格 bbox 未提供则为空，不能冒充准确单元格坐标；图像通过原件区域重新渲染，不跟随模型输出路径。
- `KnowledgeStore.ingest()/document()/formula_records()`：SQLite 独立 `document_elements` 记录，并补入可检索公式/表格行 Chunk。替换/删除级联清理旧记录；原件、观察摘要与版本校验拒绝陈旧证据。既有 original/visual-evidence 接口保留原始视觉区域；已有 HTTP document 接口返回结构，真实 HTTP ingest/query 已运行。
- `formula_conversion.convert_latex_arithmetic()`：显式四则、分式、简单幂/下标/命名符号的受限转换。隐式乘法、求和、积分、根式、复杂下标及任意命令拒绝转换，仍保存检索证据。转换成功不授予计算权限。
- `KnowledgeStore.review_formula()`：仅受控本地私有文件审核，钉住原件SHA、观察摘要、区域PNG摘要、审核人、参数说明引文和安全AST；没有HTTP审核/模型晋升入口。`DependencyAgent.document_formula` 对结构后端只接受唯一已审核记录；未审核公式不得走旧字符串解析捷径。生成证据入口同样拒用未审核结构观察；记忆原有 txt-only 人工审核规则保持。

收尾反例确认：相同原件SHA重解析会清除审核，但旧计划仅检查原件SHA仍能到达参数处理。新增 `verify_formula_record()`，计算前后重放当前独立记录、审核回执、表达式和参数身份，旧审核撤销/替换立即拒用。修改前1个针对反例失败，修改后67项定向通过；这是审核生命周期修复，不是保留集得分调参。

配置示例（绝对路径属于部署配置，不能由请求指定）：

```bash
export ICT8_PDF_BACKEND=mineru
export ICT8_MINERU_EXECUTABLE=/tmp/omni-mineru-venv/bin/mineru-kit
export ICT8_MINERU_HOME=/tmp/omni-mineru-models
```

默认 native 兼容路径保留。隔离环境安装/权重不进入应用依赖；模型权重固定 revision、LFS SHA核验与下载记录在 runtime。MinerU代码是带商业规模/署名附加条款的 Apache2基础许可，不能称普通Apache2；模型源许可见其manifest。Docling代码MIT，layout模型Apache2，TableFormer/CodeFormulaV2模型CDLA-permissive2；数据研究使用限制独立于工具许可证。提供服务须保留本页 MinerU 署名。

## 复现与验收边界

`run_pdf_parser_trial.py` 在各独立环境运行相同原件；`evaluate_pdf_parser_trial.py` 持久化预测后读取未修改官方参考。IoU≥.5区域匹配、LaTeX空白归一编辑相似度/严格匹配、单元格文本与跨度严格匹配及匹配块阅读顺序是诊断协议；不是CDM/TEDS官方榜单评分。

`evaluate_real_document_retrieval.py --structured-backend mineru --native-ocr` 实际走生产入库检索，固定Top4，沿用旧OHR scorer；不把检索/解析成绩称答案准确率。`verify_structured_pdf_integration.py` 验证真实HTTP入库、独立记录、检索、原件与裁剪回放及未审核计算拒绝。具体结果与失败见 `REAL_DOCUMENT_RESULTS.md`。

## 2026-10-11 真实视觉答案闭环补充

这次在配置私有文件中发现并使用了用户授权的 DeepSeek `deepseek-flash`；保留独立研究客户端，不更改服务端全局模型配置。客户端复用应用原有 PDF 图像校验/大小限制，把可信原页图转换为 DeepSeek Chat Completions 图像输入，并校验响应模型身份。官方接口支持 Flash 图像输入，见[DeepSeek Vision 文档](https://api-docs.deepseek.com/guides/vision/)。

`route_visual_source_fallback()` 现在允许明确声明 `supports_visual_source_review` 的该客户端使用既有原页视觉路径：模型先从原始页图选取逐字片段和位置，再独立复核完整问题、数值、行列关系及位置；服务端重验原件SHA、渲染SHA和坐标。单一金额问题只有一个完整币种金额，或其他“金额/总额/拨款”等问题只有一个完整数字字面值时，在审核通过且行名等支持片段仍保留为引用的前提下，显示答案投影为该值。复合问题仍显示全部片段；结果的 `calculator_input_eligible` 仍为 `false`。

真实 OHR 开发题实跑、失败复跑和原页重放记录见 [REAL_DOCUMENT_END_TO_END.md](REAL_DOCUMENT_END_TO_END.md) 与 `runs/real_document/real-agent-finance-20261011-*.json`。Sales/Use Tax完整链路一次命中 `$ 343,215,345`，归一化EM/F1为1/1；Highways & Streets CIP选择与复核成功，数值投影后的EM/F1为1/1；Events Center债务问题有依据地回退。Sales/Use Tax另一次完整链路也安全回退。独立原页分支重放核对3个原页引用区域、原件/渲染/问题SHA和8项独立复核。这些是同一PDF上有限开发题结果，不是基准整体准确率。API官方计费说明见[DeepSeek 定价](https://api-docs.deepseek.com/quick_start/pricing/)。
