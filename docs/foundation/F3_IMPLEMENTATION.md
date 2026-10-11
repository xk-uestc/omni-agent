# F3 生产 RAG 改进（2026-10-11）

仅 `memory` 分支；单一执行 Agent，无生成模型、付费调用或训练。执行前 fetch 成功，本地与远端为 `bd666d5efdcedd5167c65136e190136114553a1b`，它是指定 `d61ce23dfa32fcb2be01d6b71235f3e48e693aed` 的后继，新增三次评测/文档提交，生产代码未变化。未跟踪 `Qimem/` 未修改。

## 1. 正文来源条件绑定

修改 `KnowledgeStore.search()`，新增 `source_binding.bind_sources()`：检索前按来源版本汇集正文声明，区分声明年份/版本与普通年份提及、文件自撤销与其他文件撤销、对象排除与关键词、明确机构与邻近/附属机构。约束收据保留 chunk、页、locator、SHA、原文；不用 filename/title 证明身份。精确编号改为仅检查正文。正文同系列存在多个未撤销版本而查询未指定版本时，相关来源拒用并输出 `requires_clarification`，不自动选最新版。

这些是保守语法支持，未知条件不证明匹配；不是通用语义理解或答案证明。未改变 EvidenceCoverage 的事实验证权限、SQL、记忆或 scope 规则。

修改前：5个小反例4失败（撤销版本、邻近机构、标题编号、版本未指定），见 `runs/f3/binding-before.txt`。开发集立即使用未改动 F2 scorer 对照，结果见 F3_RESULTS。测试命令见结果文档。正式保留集在全部候选实现冻结后运行。

## 2. 来源内对象关联

修改 `KnowledgeStore.search()`，新增 `linked_evidence.expand_identifier_scope()`：先保留正文精确编号种子，再允许经过 SHA 校验的单对象首页面声明、同原页单对象、相同显式章节单对象或明确次页续说明。来源内出现其他编号则不能扩大该范围。范围预算最多12个原文页，实际文档页数来自 ingestion stats；索引丢弃重复页眉不能降低预算。所有新增 chunk 附 `identifier_scope_link` 原文关系收据，未经证明的关系继续使用原精确过滤。

真实开发改善是三道现行公报排除条件题，由首页面对象声明得到第4页否定限定。长册第3页编号、第9页例外没有显式关系，不猜测关联。5项针对续页/其他样本/无关系/撤销/更新/预算的测试和旧安全测试共42通过。首次预算测试失败暴露重复页眉导致索引页数不足，已改用原文页数；前后日志保留。

## 3. 有界原始表链接入生产

新增 `bounded_table_evidence.prepare_table_bundle()`，沿用 F2 的对象解析、唯一表来源、原页表头/行清单、`page_scope`/`row_scope` 和固定预算方式，移出隔离脚本进入 `KnowledgeStore.prepare_evidence()` 与 `_answer_hits()`。适用范围是明确枚举且有原生 pipe 分隔、重复精确表头的 PDF 表；一般表布局仍使用原 native_row_selection 路由。不下载模型，也不新研究表链模型。

支持显式对象编号或正文首面机构→对象声明。至多4个提名来源逐一检查，不在第一个正例后停止；只将匹配身份页交给生成器。一次额外有界检索保留当前年份、机构、版本和 document_id。当前索引中存在竞争表来源、不同样本、重复行身份、表头/单位变化、日期范围冲突、未解析行或不支持的比较/否定筛选时拒用。原页完整保留未匹配行和脚注，筛选仅是字面导航，不能代替独立语义审核。

每次准备最多6次原页读取、32000原文字、一个额外检索。输出保留原始行 quote/offset、表头、字段、日期、SHA、页码和范围账本，calculator_input_eligible=false。Top-K保持4，不改 Gold/scorer。`source_answer_dossier.build_dossier()` 收到 bundle 后重新构造并逐字段比对；原独立整题审核、原文引文定位及 `replay_context()` 仍生效。每次重构也受同样预算：有审核器时准备+重构最多2个新增检索/12页，最后既有 quote replay 另最多6页。成本区分首次准备与重构/最终审核读取，不能把重读伪装成零成本。

无审核器时生产 answer 返回 bundle，并明确 `bounded_evidence_pending_review` / insufficient_evidence，不生成未经验证的全表事实；因此本轮真实答案模型成绩均not_run。确定性测试审核器实际消费两页、多条原文行，通过现有引用重放；这是机制测试，不是答案准确率。

新增未指定竞争版本时的生产澄清响应 `source_version_required`。表链更新反例首轮45通过/1失败，原因是 source pin 在候选丢失后才校验，已提前验证初始 hits。最后三项能力和既有 dossier、table、SQL、HTTP与记忆安全定向156通过，日志 `runs/f3/table-tests-frozen.txt`。
