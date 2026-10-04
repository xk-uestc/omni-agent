# 第八轮独立审查（2026-10-04）

## 结论与审查范围

本次只读审查发现：FINAL2 的剩余失败包含字段角色识别、完整请求误判、模型澄清或复核误拒、物理来源契约、查询性能，以及尚未记录具体错误码的执行失败。不能把这些失败统一解释成模型答错或本机超时，也不能用专项通过宣布三个方向全部完成。

终检还发现一项需要修复的 P1 范围语义问题：用户明确否定的行数限制仍可能成为实际 SQL LIMIT。详见下节；它与模型误拒和评分来源契约不同。

审查依据为公共用户问题、实际运行响应、生产代码及历史运行对比。没有读取 `evaluation/v2/private`、冻结参考答案、评分 reference 文件、运行凭据或 `runtime/model_config.json`；没有调用真实模型 API。本审查没有修改冻结后端、测试或评分器，仅新增本文。报告中的已有评分仍保持原口径，不通过新增题目专用词典或调整评分器提高通过数。

## 证据文件

- 复杂文档专项：`docs/ROUND8_COMPLEX_PROBES_FINAL2_20261004.json`。
- 无文字层页图专项：`docs/ROUND8_RASTER_DOCUMENTS_FINAL2_20261004.json`。
- Sakila 完整结果交付：`docs/SAKILA_ROUND8_ARTIFACT_DELIVERY_FINAL2_20261004.json`，45/56；完整结果评分与历史 inline 评分不同，不能直接替换比较。
- Sakila 实际响应：`D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/round8-final2-development-20261004/observed.jsonl`。
- 公共问题：`D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/evaluation/v2/system_input.json`。
- 历史对比：`docs/SAKILA_ROUND8_ARTIFACT_DELIVERY_RELEASE_20261004.json`及对应 `round8-release-development-20261004/observed.jsonl`；对比只使用历史生成 SQL 和运行结果，没有读取其参考答案。
- 生产路径：`ict-track8/backend/native_table_question.py`、`visual_source_answer.py`、`nl2sql/complex_query.py`、`nl2sql/schema_profile.py`、`nl2sql/value_index.py`、`sql_history_scope.py`、`omni_agent.py`。

## P1：否定行数限制被当作肯定限制

使用当前冻结代码的 `configure_complete_scope(QueryPlan(), question)`进行纯函数隔离诊断，未运行模型或使用参考答案：

| 原问题 | 实际 scope 结果 | 判断 |
|---|---|---|
| 不要只返回5行，列出全部invoice的id | `semantic_row_limit=5` | P1：用户拒绝 5 行上限，但程序把它作为肯定的最终结果上限 |
| 不要返回5行预览，列出全部invoice的id | `semantic_row_limit=None`、`requested_preview_limit=5` | P2：完整 SQL 范围未缩小，但用户否定的预览上限仍被采用 |
| 列出全部invoice的id，不限制为5行 | `complete_result_scope_ambiguous_or_unsupported` | 范围语言覆盖缺口，正确要求被拒绝 |

当前 `enforce_relational_result_scope`已经解决“模型自行加上限”或“遗漏用户肯定上限”的执行绑定问题，但它会忠实应用错误解析的 `semantic_row_limit=5`，因此不能代替原问题极性判断。该情况会把被缩小的 SQL 结果标为完整，应在发布前修复。

最小修复是对行数约束匹配做局部否定判断，区分“不要/无需/不限制（为）”与真正肯定的“只返回/最多返回”。不要删除整条含否定的句子，不能顺带移除实际筛选条件或另一个肯定限制。补充否定 cap、否定 preview、肯定 cap、同句正反约束冲突的反例测试；不认识的冲突继续澄清，不能猜测。

日期格式终检确认：数字数量级目前只保留 `numeric_encoding_hint`且 `format=unknown`、`numeric_unit_verification=not_proven_by_magnitude`；混合/未知/预算耗尽没有升级为 ISO。没有发现它继续把 YYYYMMDDHHMMSS 声称为已验证 epoch 的旧问题。

## Sakila 的 11 项失败分型

| 案例 | 已证实的现象 | 分类与边界 | 通用修复方向 |
|---|---|---|---|
| cs-c02 | 两次 SQL 模型调用成功，外层只保存 `SqlSafetyError`，没有结果 | 执行失败原因未记录；不能断言是 deadline、行数限制或模型 SQL 错误 | 保存服务端固定错误码与执行阶段，保持失败计入分母；补充诊断后再决定修复 |
| cs-c05 | 2607 行完整 artifact 认证成功；`physical_projection`失败 | 精确物理血缘契约不匹配，不等于已证明数值错误 | 输出实际 AST 血缘；需要等价来源时建立严格、独立的来源等价证明，不伪造 metrics，不改冻结评分 |
| cs-f11 | `staff_id`被识别为第二个指标，触发 `ambiguous_metric`，模型给出的分组+SUM计划被规则兜底拒绝 | 真实字段角色识别/路由缺口 | 在实际物理字段的分组语境中把 ID 识别为维度；必要时让完整物理分组请求进入关系 SQL 通道 |
| cs-f12 | payment/rental 各自日期条件保留，COUNT(*)+SUM完整执行；只有 `required_source_reads`失败 | 来源读取契约不匹配；不是已观察到日期过滤遗漏 | 区分行数 COUNT(*) 与具体列读取。仅在非空保留侧键证明成立时，可使用显式 COUNT(key)及真实列读取；不得凭评分失败补无关读取 |
| cs-r01 | 规划器询问“用时天数”是时间戳间隔的小数天还是整日日期差 | 默认日期运算契约不明确引起保守澄清，非执行错误 | 为未指定取整的时间间隔明确统一默认：原生 elapsed fractional days；保留用户取整要求、时区/格式冲突澄清 |
| cs-r04 | 两次 SQL 模型调用成功，外层仅保存 `SqlSafetyError` | 与 c02 一样缺乏执行错误码，暂不能进一步归因 | 固定错误码/阶段诊断；不得通过增加执行预算掩盖原因 |
| cs-r06 | 16 行完整 artifact 认证成功，两个 COUNT DISTINCT 保留；`physical_projection`失败 | 与精确 COUNT 来源表有关的血缘契约失败；尚未以参考答案证明数值错误 | 保留真实输出血缘，明确用户指定来源；不要为了通过评分把 actor 来源直接改写为 film_actor |
| cs-r08 | `complete_result_step_budget_exceeded`，50,000,000 步，观察到 0 行；生成相关 NOT EXISTS | 真实查询性能失败，不能归为单纯本机 deadline | 先物化正付款的去重 rental_id 键集合，再 LEFT anti-join，保留 NULL 与 amount>0 条件；预算不扩大 |
| cs-s2-t3 | 服务器判完整新范围，但模型重新索要分组/输出字段 | 完整请求的模型过度澄清 | 未请求分组的明确记录计数应为单值聚合，不能要求用户额外指定分组 |
| cs-s2-t5 | 服务器判完整新范围，模型仍索要 2006 年具体起止及分组 | 明确年份和记录数被当作前文省略 | 用既有日期解析产生经核验的全年半开区间；明确没有新增分组要求 |
| cs-s3-t3 | “R评级”没有取值槽位，随后进入仅允许单项替换的追问协议并被拒绝 | 通用语义别名与完整关系查询识别缺口 | rating 的规范“评级”语义关联实际列；完整显式筛选与关系输出启动新查询，不放开真正省略的复合追问 |

### c05 / r06 的来源口径边界

c05 在 RELEASE 的成功 SQL 使用 `COUNT(DISTINCT film.film_id)`；FINAL2 使用 `COUNT(DISTINCT film_actor.film_id)`，实际 INNER JOIN 链与维度来源相同，且完整输出行数同为 2607。变化与物理投影失败吻合。它证明了“来源角色口径发生变化”，不能单独证明数值错误。

r06 实际 SQL 按 `category.name` 分组，分别去重 `rental.rental_id`和 `actor.actor_id`，保留 LEFT JOIN 与两种去重计数。其 16 行完整结果认证成功。公共问题没有把 actor_id 指定为某一表的列；未读取冻结物理角色，不能据此认定哪个来源表就是私有评分要求。继续保留其评分失败，不把“来源可能等价”作为自动通过依据。

若后续建设来源等价机制，必须检查实际 JOIN 类型、完整连接键、保留侧与 NULL 扩展、类型/排序规则、计数去重语义。不能笼统地认为 FK 两端的 COUNT DISTINCT 永远等价，不能只替换 plan 元数据而保留另一来源的 SQL。

### r08 的性能修复

实际生成 SQL 对每个 rental 记录执行 payment 的相关 `NOT EXISTS`，过滤条件是匹配 `rental_id`且 `amount > 0`。这次触发的是步骤预算，报告并未仅给出墙钟超时。

通用候选表示如下，来自问题本身，不是参考答案：

```sql
WITH paid AS (
  SELECT DISTINCT rental_id
  FROM payment
  WHERE amount > 0 AND rental_id IS NOT NULL
)
SELECT r.rental_id
FROM rental AS r
LEFT JOIN paid AS p ON p.rental_id = r.rental_id
WHERE p.rental_id IS NULL
ORDER BY r.rental_id;
```

这个表示只排除存在正付款的关联键；payment 中 NULL rental_id 不会污染反连接。应先通过现有 AST/FK 校验，再用执行计划确认键集合只扫描一次，并在原预算下执行验证。不得改动官方数据库添加索引来冒充生产算法改进。

在实际公共 Sakila 数据库上进行了只读 `EXPLAIN QUERY PLAN`及现有 `validate_proposal`隔离诊断：原 SQL 为 `SCAN r → CORRELATED SCALAR SUBQUERY → SCAN p`；上述表示通过静态校验，计划为 `MATERIALIZE paid → SCAN payment → DISTINCT temporary B-tree → SCAN r → SEARCH p USING AUTOMATIC COVERING INDEX LEFT-JOIN`。这证明避免了相关 payment 重复全扫的表示可用；尚未据此宣称真实模型已生成它、整题已通过或完整执行时间已达标。

## 连续会话的最小规则建议

### s2t3：显式更换日期口径

公共题文：

> 还是2005年7月，但现在以return_date而不是rental_date统计归还记录数，不保留租出月份过滤。

实际槽位有 `2005年7月`，以及真实 rental 表的 `return_date`和 `rental_date`字段；服务器已标 `self_contained_sql`。当前失败来自关系规划器要求分组，而题面已经给出指标、时间和日期口径，未要求分组。

最小规则：识别经 Schema 核验的“以 X 而不是 Y”日期字段切换，结合已解析的月份形成当前独立请求：只对 X 应用该月半开区间，明确取消 Y 的原月份限制；没有显式“按/各/分组”要求时输出全范围单值记录计数。不要继承旧日期过滤，也不要为标量 COUNT 虚构分组缺口。不能据此把所有带“仍/还是”的短句视为完整查询。

### s2t5：明确全年范围与指标替换

公共题文：

> 仍查2006年payment_date范围，换成payment记录数。

实际槽位已有 `2006年`、payment 的记录计数指标及 `payment_date`，服务器也已标 `self_contained_sql`。模型却要求具体起止范围与分组。

最小规则：复用现有日期解析器把明确年份解释为该字段上的全年半开区间；存储形式仍由实际快照日期探针决定，不能把 unknown 声称为 ISO。题面没有分组请求时按单值记录数处理，不因“仍查”要求不存在的前文分组。

### s3t3：明确筛选 + 新的完整列表

公共题文：

> 仍是R评级，列出每个film_id的inventory_id副本数量，无库存也保留0，按film_id升序，全部返回。

实际槽位诊断得到 `values=[]`。`value_index.py`只允许单字符取值在已有字段关联时匹配；`schema_profile.py`的 rating 规范别名只有“评分”，因此“R评级”没有关联到真实 rating 列。随后请求落入 `_model_followup`的单项替换协议，新增粒度、计数、零值、排序与完整结果被当作多项不受支持的追问。

最小规则：补充通用 rating→评级语义，由真实字段及取值索引核验 R；明确关系列表、当前筛选和输出要求齐全时启动独立范围。保留单项协议对确实依赖未提供条件的追问保护。不要新增 film/R 专用题目字典。

## 复杂文档差值与页图单位

### Network 跨年差值

实际题文为 `For Network, subtract its 2031 cost from its 2032 cost.`。native 表格选择与独立审核均完成真实 HTTP 200；审核 `approved=false`，但其他五项完整性、实体/期间、行列选择、竞争来源及单位符号检查均为 true。服务端已先完成 `annotation_arithmetic(..., allow_column_comparison=True)`，然后因独立审核拒绝返回不足证据。

这属于复核输出不一致或额外未诊断的语义拒绝。不能忽略 approved，也不能改为“其他 true 就自动放行”。最小改进是规范审核协议：明确 approved 必须与逐项判定一致；拒绝时给固定原因码。保留原候选选择、服务端操作方向、单位/期间与有界诊断记录。若进行一次有界一致性重审，必须对同一原问题、同一来源与原候选独立重审，不能在失败后放宽单位、换数或丢弃子问。

### raster borderless

FINAL2 确实执行了原页图像选择及独立审核，均为 HTTP 200；审核拒绝项包括 `whole_question_answered`、`all_requested_items_present`、`units_signs_periods_preserved`和 `source_relationship_explicit`。因此不能归因为未读取图像、网络故障或本机 deadline。

当前失败报告没有保存被拒的完整候选片段，不能只凭这些检查断言具体是哪一段单位被遗漏。通用修复应让量值片段与单位表头分别明确定位，保持同一列/同一期间/同一实体的关系，审核最终返回的片段是否实际包含必要的币种与倍数，而不是只看到 bbox 内有单位就认为回答保留了单位。

建议在选择协议中区分 value / unit / entity / period 片段职责，并记录有界失败原因；静态层检查结构与来源版本，语义层保持独立审核。没有明确单位声明时应保留“来源未声明单位”，不得为满足格式制造单位。不要让视觉文字直接进入正式数值计算器。

## 后续验收要求

1. 等冻结运行结束再修改后端，已有 FINAL2 报告保持原字节与失败分母。
2. 每个修复先验证通用触发与反例：ID 分组不是计数、完整新查询不会继承旧过滤、真实省略仍澄清、NULL 反连接不退化为 NOT IN。
3. 重新冻结后按新报告名运行真实模型与回归，保留原预算；只跑修复题不作为完整泛化结论。
4. 继续分别报告 inline、完整 artifact、文档 literal/视觉复核与独立会话指标。任何来源契约失败都先解释其含义，不改评分来隐藏失败。
5. 模型复核、静态来源校验与实际运行是不同层次证据；不得把独立模型批准表述为正式语义证明。

## 最终冻结补丁复核附录

本附录为最终补丁的只读审查，不覆盖或删除前文历史失败。没有修改后端、测试、评分器，也没有重新调用真实模型、读取凭据或私有参考答案。

### 已复核通过

- 对 `configure_complete_scope`执行 15 个纯函数诊断，覆盖已支持的中文/英文局部否定、否定预览、明确肯定的另一数量、正反同值冲突、保留业务数值过滤、零数量、矛盾数量与每组最新一条；15 项均与预期一致。
- `按payment.staff_id分组求payment.amount总和`进入关系规划；`求payment.amount平均值`、`计算平均销售额`与普通业务分组没有被新增物理路径规则抢走。
- `engine.py`仍要求 `required_intent is None`才能进入复杂关系规划，原跨来源约束渠道未被放松。
- AST 层最终 LIMIT/未授权 OFFSET守卫仍存在；日期未知格式不再仅凭数量级宣称 epoch；视觉片段强调实体、数值、列头/单位实际可见对齐；独立审核 boolean 守卫仍保持拒绝能力。
- 父代理报告相关 54 项测试通过，这是父代理的执行证据，不冒充本子代理重新运行整套测试的结果。

### 剩余 P1：未支持的否定表达仍被当成肯定上限

以下独立构造的反例直接运行当前纯函数，每个结果均为 `semantic_row_limit=5`且 `complete_results=True`：

| 输入 | 语义问题 |
| --- | --- |
| `不想只返回5行，列出全部invoice的id` | 不想要的五行上限被应用 |
| `不希望只返回5行，列出全部invoice的id` | 不希望的五行上限被应用 |
| `don't just return 5 rows, list all invoice ids` | just 使局部否定解析未命中，肯定子串仍命中 |
| `不要仅仅返回5行，列出全部invoice的id` | 仅仅 使局部否定解析未命中 |
| `不要限制返回5行，列出全部invoice的id` | 限制返回 使局部否定解析未命中 |

另 `不能不返回5行`输出 `semantic_row_limit=None`且 `complete_results=True`，表明双重否定不能直接套用单次否定剥离。

这是同类截断缺陷的剩余通用触发条件，不是要求增加这些具体句子的题目词典。建议在明确支持的局部表达外，对数量请求附近尚未消费的否定/双重否定结构返回固定澄清错误；尤其不能在存在否定量词的情况下，提取内部 `返回5行`子串后宣称其为用户授权。明确的肯定数量、其他业务过滤以及已支持局部否定应保留原行为。真实模型复核不能替代服务端结果范围守卫。

这项 P1 已直接通知父代理；冻结状态下本子代理没有修改实现。其他审核建议与 FINAL3 真实模型收益仍应按各自证据口径报告。

### 日期、完整结果与视觉来源的专项终检

按父代理要求进一步检查 `date_profile.py`、`engine.py`完整结果分支、`result_artifact.py`和 `visual_source_answer.py`，未发现除前述否定数量之外新的实质 P1。

- 本子代理实际运行现有日期存储、复杂关系预算耗尽、视觉来源回退定向测试，结果 **15 passed in 57.35s**。使用独立 D 盘测试临时目录，没有修改测试或调用真实模型。
- 另用内存 SQLite 做七类日期存储诊断：合法闰日/ISO为 `iso_text`；非法日期、混合文本、紧凑数字时间、全NULL、带时区文本与ISO/epoch混合均为 `unknown`。紧凑数字只有未验证的数量级提示，没有 epoch单位证明。
- 完整结果分支只有游标 EOF、查询绑定、源文件/未checkpoint WAL及生产代码再次核验成功才发布完整元数据和服务端签名；预算失败保留 `incomplete`/`partial`、`row_count=None`和无可分页 artifact_id，不把预览冒充全部。
- 视觉回退调用真实注册原件渲染，渲染前后核对原PDF SHA；两次模型调用后再次核对源版本和PNG/manifest签名。引用bbox须有限且有序，独立审核八个boolean均须为true，答案明确为独立视觉模型审核，且 `calculator_input_eligible=False`。没有把模型语义批准冒充原生文字精确匹配或正式数值来源。

边界仍保留：不支持时区的日期探针会要求进一步明确；两页视觉范围不是整份长文档的穷尽验证；独立模型审核不是形式语义证明。这些是明确暴露的覆盖限制，不是本次发现的错误结果或来源核验绕过。

## FINAL4 修复状态复核

以下结论区分历史缺陷、当前服务端修复及模型可用性，不修改历史运行成绩：

| 项目 | 当前状态 | 证据与界限 |
| --- | --- | --- |
| 模型擅自应用/遗漏最终行数上限 | 已修 | `enforce_relational_result_scope`绑定原用户上限，拒绝无授权 LIMIT/OFFSET |
| 数字时间仅凭数量级认定 epoch单位 | 已修 | `format=unknown`及未证明单位的数量级 hint；七类日期诊断符合保守预期 |
| 否定数量、否定预览被应用为肯定上限 | 已修 | 已支持局部否定正确移除；冲突返回错误；FINAL4补充未识别否定和双重否定门控 |
| 本报告五个否定变体及一个双重否定反例 | 已修、独立复核通过 | 六个输入均返回 `complete_result_negative_scope_ambiguous_or_unsupported`，且 `complete_results=False` |
| 明确肯定上限与已支持否定 | 保持可用 | 三个原有控制输入独立复核通过：纯肯定5行、否定5行后肯定8行、否定5行后全部返回 |
| ID字段分组、明确全年/月、日期字段切换、反连接性能 | 已补通用规划提示/路由；整体效果待冻结实测 | 不是形式语义证明，也不将源码提示改进自动计作真实模型通过 |
| rating→评级字段绑定 | 已补通用别名及实际值核验 | 不能宣称所有简短多轮追问均可正确承接 |
| 视觉实体/列头/单位缺失与审核不一致 | 已补选择/审核提示；仍属模型可用性 | 保留所有boolean拒绝守卫，不把拒绝直接改为通过；真实模型成功率由各FINAL报告独立呈现 |
| 源文件版本、渲染签名、完整游标与预算耗尽 | 未发现新增实质P1 | 已完成专项源码审查和15项现有定向测试 |

FINAL4的本次复核只运行上述六个历史反例及三个原有控制输入，**9/9通过**；没有继续枚举新措辞，没有修改生产代码、测试或评分器，没有调用API。父代理另报告52项定向测试通过。原FINAL2/Sakila失败分类与OHR分数继续保留为历史实测；物理来源角色口径、独立模型误拒、长文档非穷尽页范围以及真实连续会话正确率仍须按实际验收结果判断，不属于本子代理确认已消失的模型能力限制。

### 最末点号守卫补丁独立复核

父代理进一步发现物理字段点号被当作句号，可能使“不想只对invoice.id返回5行”误授权肯定上限。修复后独立只读诊断 **7/7**：包含 `invoice.id`、`invoice.amount`、`2.5` 的四个中英文否定数量/预览请求均拒绝；三个肯定5行控制仍正常。新增测试同时检查真实engine路径和无完整artifact落盘。未发现新实质P1，未改文件或调用API。

保留全量3044通过/2项时间门禁失败与同源码两项复测通过两个事实，不把复测或源码一致性审计当作完整回归通过。
