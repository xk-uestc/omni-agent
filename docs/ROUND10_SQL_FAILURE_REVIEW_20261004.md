# ROUND10：复杂 NL2SQL 未解决失败只读调查

当前证据支持优先修复日期探针覆盖和字段角色解析；不能把模型/API故障、结果血缘合同差异及查询总体歧义统称为 SQL 错误。本文只读调查，不修改生产源码、测试、模型配置或评分器，不调用 API。

## 核对范围和成绩口径

- 最新 Sakila 完整结果交付报告：`docs/SAKILA_ROUND8_ARTIFACT_DELIVERY_FINAL4_20261004.json`，46/56，五轮会话 19/20、3/4 段完整通过。
- 同一批观测的历史 inline 合同：`docs/SAKILA_ROUND8_FINAL4_DEVELOPMENT_20261004.json`，8/56。两个合同不能混写成同口径提升。
- 直接观测：`D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/round8-final4-development-20261004/observed.jsonl`。本次只读 SHA-256 核对为 `58ad540ebe9470f65ba469e990d62f88d5c71e391f376e97ec520c428109b178`，与交付报告一致；RUN_MANIFEST 为 `9acd8c46b720c5fb6c6763516fb874b848c78b6a04fd67b497d551bac2742544`，同样匹配。
- ROUND9 增量混合专项 `ROUND9_COMPLEX_PROBES_INCREMENTAL_20261004.json`：12/12，其中 8 项 SQL、3 项模型文档和1项跨页公式生产工具；不能称 12 项均为真实 SQL 问题。
- ROUND9 首次随机 Schema 专项 `ROUND9_FRESH_SCHEMA_FINAL_20261004.json`：6/6，源码起止稳定。小型、有明确物理字段提示的自拟问题不是独立盲测，也不能替代 Sakila 多表复杂题。

## 十个失败逐项分类

| case | 问题与实际失败 | 可确认归类 | 未证明事项 |
|---|---|---|---|
| cs-c05 | 演员×类别的不同电影数。提案和一次结构修复均 HTTP200/completed；拒绝 `complex_query_join_not_actual_fk_or_shared_key` | 本地结构守卫拒绝了模型候选 | 原 SQL 未存入观测，不能断言具体是哪条 JOIN，亦不能断言应放宽守卫 |
| cs-c06 | 店铺×类别的不同电影数和库存副本数。提案200，独立review **502/failed/model_verified=false** | 上游模型审核不可用 | 不是已证实的字段/SQL语义BUG；不能把502叫本地执行超时 |
| cs-f09 | 明确按 **payment.customer_id** 输出全部599组。SQL却投影 **customer.customer_id**，LEFT JOIN payment；完整文件599行真实核验，但 frozen physical projection失败 | 物理输出来源被替换；模型独立审核未发现 | 当次数值接近/相等不证明跨Schema语义相同；无付款实体会使范围变化 |
| cs-f10 | 全部城市付款总额、无付款保留NULL。提案及review均200，观测只留 **SqlSafetyError**，response为空 | 执行/交付通道拒绝，细因丢失 | 缺安全错误码与候选SQL，不能声称扇出、时间预算或行数上限是根因 |
| cs-f11 | 明确 payment.payment_date 的2005年7月、各staff_id、amount合计，并明确不按租赁/归还日期过滤。被拒为 **ambiguous_metric** | 字段角色解析存在生产缺口，当前源码可离线复现 | 不能把已明确的问题归因用户缺口径；尚未实测补丁效果 |
| cs-f12 | 租赁日期与付款日期分别过滤后计数/求和。SQL数值执行成功，COUNT(*)血缘锚到 rental.rental_id，而冻结输出角色要求 payment.payment_id COUNT | 输出血缘合同不满足；COUNT(*)加入行计数与业务记录计数区别须保留 | 单库当前相等不足以把所有JOIN COUNT(*)自动改写为payment计数 |
| cs-r01 | 2005年8月租出、return_date非空、记录数/客户去重/平均归还天数。仅return_date被探针覆盖，rental_date未覆盖；提案要求补格式 | 生产探针发现范围不完整导致模型拒绝；离线已复现 | 没有探针不能直接允许ISO文本比较；需真实扫描而非忽略检查 |
| cs-r05 | 未归还租赁按客户国家统计，五表关联。提案/review均200，观测仅 **SqlSafetyError** | 执行/交付拒绝，缺精确诊断 | 没证据指向超时、SQL扇出或模型审核误拒 |
| cs-r06 | 类别的租赁去重与演员去重。LEFT链保留未租赁电影演员，Action实际actor=166，而参考164；rental数一致 | 查询总体/关联保留范围不同，且题面演员总体可能存在歧义 | 不能仅为跟参考吻合硬编码INNER或排除无租赁电影。需独立明确两指标总体后测试，旧失败保留 |
| cs-s1-t2 | 明确2005年收款、改按payment.staff_id求amount合计。审核说SQL符合但没有payment_date探针，filters_and_dates=false | 生产探针覆盖不足导致审核拒绝；当前源码返回[]，离线已复现 | 后续ROUND9六题成功不证明此会话题已修复 |

## 当前源码的只读复现

使用 `sqlite3.connect(...?mode=ro, uri=True)`读取真实 Sakila，不写数据库、不调用模型。导入生产 SchemaIntrospector、storage_profiles 和 SchemaLinker：

1. cs-r01 原措辞下 `storage_profiles`只返回 `rental.return_date`，whole-column 证明iso_text，非空15861、空183。没有rental.rental_date。
2. cs-s1-t2 原问题下 `storage_profiles`返回空数组。
3. cs-f11原问题下探针能取得payment.payment_date，非空16049、空0，但 SchemaLinker 同时产生：
   - payment.amount → metric；
   - payment.payment_date → filter；
   - **payment.staff_id、rental.staff_id、staff.staff_id → metric**；
   - **否定片段里的“归还日期”→ rental.return_date dimension**。

源码关联：`nl2sql/date_profile.py:16`只扫描题面出现的物理日期列名；`question_roles.py:208`的group_fields只识别“按…分组/统计/计算/汇总”，不覆盖“各staff_id”；`planner.py:153`/`:193`把多个metric识别当作歧义。`complex_query.py:42`的路由主要是复杂关键词、多物理owner或“按table.column分组”，cs-f11不因此必然进入复杂通道。

## 三项通用补强建议

### 1. 日期探针由查询依赖驱动，保持真实快照和预算

两阶段收集：先按已明确表/实体和原题日期角色扫描相关候选；候选SQL静态合格后再从AST提取实际使用日期列，为独立审核补足快照探针。没有必要扫描整库所有日期列，也不能因为数据库列多使真正相关日期列排在max_columns之后。所有probe保持whole-column、原事务、时间/步骤预算；超预算继续unknown并明确缺哪列。

验收至少包括：自然语言“2005年收款”、只写终点却计算起止时长、表数很多且相关日期列排名靠后、同名日期列、多日期不同角色、混合T/空格、NULL、numeric epoch未知单位、时区未知，以及对比/排序精度。候选依赖探针只能证明存储和解析，不能替模型决定业务日期角色。

### 2. 先明确字段角色和投影合同，再进行规划及独立审核

补“各X/每个X”分组语法的局部作用域，并用原题明确主表绑定未限定的分组字段；只有真实唯一绑定才采纳。“不按Y日期过滤”应记录为排除日期过滤的约束，不能当正向指标/维度，也不能全局删掉否定文本。正向“按X而不按Y”、否定数值过滤及多子句需分别处理。

把原题已明确的物理输出列、计数主体、总体来源和NULL/补零规则提取为候选验证合同，要求投影血缘逐项一致。这样cs-f09不会把payment.customer_id替换成同值customer主键；cs-f12不会把另一侧非空锚点冒充请求计数主体。必要时让模型重新表示为COUNT(payment.payment_id)，而非在评分器猜测等价。

风险：主外键当前同值不证明任意LEFT/RIGHT/多对多结果相等；COUNT(*)是真实行数，不能无证据装扮成COUNT某业务列；“每个”并非总是分组（每个最新记录等）；否定作用域不可扩大或误删条件。测试要包含同名ID、无匹配实体、NULL外键、孤立事实、重复金额及非一对一关系。

### 3. 固定失败阶段与安全原因，提高多表规划可恢复性

为proposal、structural_validation、independent_review、execution、artifact_delivery分别记录固定错误码；上游502与真正语义false分开。只保留allowlist安全原因，不写原始模型正文或凭据。cs-f10/r05现有报告细因无法找回，下一次回放必须先让新观测可诊断，不能盲目调预算。

向模型显式给出已核验完整FK边/复合键和可用桥接表，当前一次结构修复应收到具体缺失edge。若模型将两个子表相同列直接连接，而实际只有经共同父表的FK，应要求其在原语义不变前提下采用可核验桥接路径；不可因为列名一致就放行，亦不可自动插父表改变孤立行保留范围。保留read-only、预算和独立语义审核全项，不对false审核自动重试到通过。

上游502的有界重试可独立讨论，但一次失败不等于本地缺功能；严禁把重试最佳分数替换原失败或跳过review批准执行。每次新回放保留原件/问题/合同与新源码哈希，完整失败分母不变。

## 下一步优先级

先完成日期依赖探针和cs-f11字段角色补丁，再用相同问题真实回放确认；同时补安全错误码，以新观测定位cs-f10/r05。物理投影合同必须保留，不改分值；cs-r06单独处理总体语义，旧题及旧失败保持不变。当前全文并未证明陌生Schema复杂查询已经全面完成。
