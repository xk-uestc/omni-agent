# F3 生产 RAG 改进（2026-10-11）

仅 `memory` 分支；单一执行 Agent，无生成模型、付费调用或训练。执行前 fetch 成功，本地与远端为 `bd666d5efdcedd5167c65136e190136114553a1b`，它是指定 `d61ce23dfa32fcb2be01d6b71235f3e48e693aed` 的后继，新增三次评测/文档提交，生产代码未变化。未跟踪 `Qimem/` 未修改。

## 1. 正文来源条件绑定

修改 `KnowledgeStore.search()`，新增 `source_binding.bind_sources()`：检索前按来源版本汇集正文声明，区分声明年份/版本与普通年份提及、文件自撤销与其他文件撤销、对象排除与关键词、明确机构与邻近/附属机构。约束收据保留 chunk、页、locator、SHA、原文；不用 filename/title 证明身份。精确编号改为仅检查正文。正文同系列存在多个未撤销版本而查询未指定版本时，相关来源拒用并输出 `requires_clarification`，不自动选最新版。

这些是保守语法支持，未知条件不证明匹配；不是通用语义理解或答案证明。未改变 EvidenceCoverage 的事实验证权限、SQL、记忆或 scope 规则。

修改前：5个小反例4失败（撤销版本、邻近机构、标题编号、版本未指定），见 `runs/f3/binding-before.txt`。开发集立即使用未改动 F2 scorer 对照，结果见 F3_RESULTS。测试命令见结果文档。正式保留集在全部候选实现冻结后运行。
