完整结果候选：隔离 e89113d 基线，待根任务审查后合并。

触发方式与范围：POST `/api/v1/nl2sql/query` 添加 `complete_results: true`。默认 false，旧 NL2SQL、Agent、跨源与已有评分器行为均不变。规则或验证后的模型计划使用同一执行器；本候选不调用真实 API、不读取新冻结 56 题。

`max_rows` 仅控制预览（最多100），默认 QueryPlan.limit=100 不再拼进完整查询的最终 SQL。原问题明确“返回3行”等要求仍保留参数化 LIMIT；DENSE_RANK Top-N 条件和 ORDER BY 保留，含并列行不被预览上限截断。不明确或冲突的用户行数约束拒绝，不能自行扩大。

只有 cursor 到 EOF 才发布 `complete_result.status=complete`。所有实际行写入 JSONL 产物，数组单元格保留 NULL、重复行、重复列标签和 BLOB（带类型的Base64）。模型与 UI 保持100行以内预览；`preview_cells` 是精确数组预览，旧 `rows` 字典接口仍兼容，重名列显示应使用数组预览。

客户端取全部行：保存首次查询返回的 `artifact_id`、`binding_sha256`、`query_sha256`；GET `/api/v1/nl2sql/results/{artifact_id}?offset=0&page_size=100&binding_sha256=<首次绑定>&query_sha256=<首次查询hash>`，按 next_offset 翻页至 null。各页都重验完整产物 SHA、列契约、源数据库/WAL内容及 generation、生产代码 hash。不能从产物元数据自行重建初始信任；同进程引擎仅保留最近256个初始绑定，重启或淘汰后客户端必须提供首次绑定。

默认完整产物预算：25000行、8MiB、5秒、50000000 VM步；源文件与 WAL 合计256MiB。引擎可显式给更严格的 ResultBudgets；最大可配置100000行、32MiB、120秒，仍有硬上限。预算超限返回 status=incomplete、result_state=partial_rows，row_count=null，不发布完整artifact，不把COUNT或预览冒充完整结果。源或源码换版直接拒绝。

UI建议：表格标题明确“显示100 / 完整19119行”；只在 artifact状态complete、EOF证明和初始绑定有效时显示“完整结果可分页读取”。预算失败显示“部分结果，未覆盖全部分组”，不能显示普通成功。详情展示查询hash、源版本、产物hash和实际完整行数，不把100行发送给模型要求推断剩余内容。

当前限制：未更改 Agent/cross-source入口及冻结评分器；本候选需要审查后决定生产启用范围。完整 artifact 在 ignored `runtime` 中；尚未实现全盘容量、过期清理或多用户权限隔离。服务器适合现有本地回环部署，不能直接宣称互联网多租户生产完备。保守源 pin 会在并发数据库更新或重启后的源generation变化时拒绝旧产物，要求重新执行。Windows无 symlink创建权限时实际链接夹具跳过，正常链接拒绝代码与目录逸出检查仍保留。
