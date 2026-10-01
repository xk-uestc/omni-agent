# 独立智能体的实现与验收边界

## 用户问题到结果

统一入口/api/v1/omni/query接收question/session_id，先读取最多五轮结构化状态。
启用指定模型时，Responses规划器选择SQL、document、fusion或clarify；输出仅为有界工具计划。
没有有效模型时保留rules_basic/rules_fallback标签，跨源问题需用户在工作台明确工具步骤。
SQL规划不能执行模型提供的裸SQL；所有计划进入同一指标、Schema、覆盖、JOIN粒度及只读安全门。
模型候选先做有界服务端规范化，再进入上述门控；端到端成绩因此包含程序的规范化与拒绝/回退行为，不能解读为裸模型准确率。接口成功、SQL值正确、模型路由通过和真实生成通过分别计分。

单源SQL/document默认使用用户原问题；只有前轮确实是SQL且服务端槽位合并已验证时才补全SQL追问，不采用模型自由改写改变年份、地区或文档主题。缺少可验证文档省略补全时保留原问题；同类跨主题的“呢”不能被错误继承成上个主题。此最终保护补丁经真实KnowledgeStore本地回归验收，晚于定向API评测，未再次做真实API调用。

Responses NL2SQL规划器通过请求局部propose(question,tables,verified_intent)接收独立规则提取的明确指标来源、聚合、业务标签、实际过滤值、半开时间窗口、分组/粒度、HAVING及澄清代码。该输入是约束而非可执行规则计划；模型仍独立提出Schema计划，并再次接受安全/语义/覆盖核验。通用callable provider保持兼容；并发请求不共享可变verified_intent。全部门控通过后，仅对同table/column/function唯一受信业务标签规范展示，保留metric ID、函数、过滤与计算，审计记录label_normalizations。

日期角色共享date_semantics.py：识别常见日期/时间字段及声明时间类型，实际ISO文本或epoch存储仍要探测。多个同样近的日期角色必须澄清；用户明说到期/入账等角色时不得被替换为别的日期。秒/毫秒过滤保留数值参数，尚未实现数值时间戳月/年分组时安全澄清，避免strftime裸整数产生NULL分组。覆盖守卫只消费已选查询图中明确出现的真实Schema表名；英文需标识符边界，字段内部的表名子串、否定主题、未选定表或未知修饰词不当作已证明主题。

统一澄清入口/api/v1/omni/clarify核对pending_question及服务端选项，保留route/state；过时选项409，伪造选项400。年份/月输入须通过日期校验；趋势时间范围与时间粒度分别澄清。Demo单价明示为订单等权AVG，加权均价不假定。前端sessionStorage保留会话ID，刷新后可追问，新对话换ID。

模型规划上下文按本轮问题检索全部文档，短追问追加上轮独立问题；最多12份文档，正文每份3000字符，正文与结构化行内容合计24000字符。相关切片/表格行优先，截断与省略行数显式标记。text只发送一次，excerpts以字符起止偏移、chunk_id及locator映射来源。无检索证据只提供有限元数据，不把无关正文当证据。规划Schema不执行全表COUNT。

## 模块与数据

| 模块 | 主要文件 | 执行契约 |
|---|---|---|
| 结构化问数 | backend/nl2sql | 自动Schema、值索引、规则/模型计划、SQL验证与只读执行 |
| 知识库 | knowledge_store.py | SQLite文档/切片，内容寻址原文件，SHA与定位信息 |
| 解析及OCR | chunk_cleaning.py、ocr.py、text_structure.py | 六种格式、真实中文ONNX、区域/bbox、质量重试 |
| 检索 | dense_retrieval.py、cross_source.py | BGE本地CLS归一化、BM25、RRF与实体编号范围 |
| 有依据生成 | grounded_generation.py、responses_client.py | 结构化claims+literal quote，数字及引用核对；失败明确摘录回退 |
| 多跳 | dependency_agent.py、evidence_fact.py | sql/search/search_fact/document_formula/cell/fact/calculate/policy_select/compare |
| 单位及政策 | formula_binding.py、unit_algebra.py、policy_evidence.py | AST计算、单位尺度、生效日期、重叠拒绝 |
| 会话 | session.py | SQLite结构化状态、TTL和容量、重启恢复、主题切换 |
| 前端 | frontend/index.html、knowledge.html、capabilities.html | SQL/引用/工具链/验收状态实际展示 |

## 多跳的可解释性

计划每步为id/tool/args，最多16步。引用格式为ref/path，依赖图来自实际引用而不是事后绘制。
执行前检查缺失依赖、循环、重复ID与未授权工具。前步结果只有通过契约后才能作为后步参数。
公式必须引用实际文档定位结果；数值必须引用SQL结果单元格或文档单元格证据，不接受literal冒充来源。
缺失、歧义、非有限数字、零分母、冲突币种或已知预测年份不符时返回incomplete，前端不将中间结果展示为最终答案。
原文件完整性错误、存储不可用、工具契约错误分别返回evidence_integrity_failed、storage_unavailable、tool_contract_failed；failed_task、skipped_tasks与实际依赖边保留，后续工具不执行。
一个DAG中的所有SQL借用懒加载、线程隔离的同一SQLite读取事务；嵌套scope由最外层释放。每步前后复核已用文档的原文件SHA及逻辑document_id版本；重入库或删除返回evidence_revision_changed，停止后续计算。成功结果source_validation列出已核对版本；它不是事实语义真实性证明。WAL写入可提交，但长任务持有读事务可能延迟checkpoint；文档-only任务不打开业务数据库。
search_fact引用前步search完整结果，重核原文件SHA、chunk、locator和逐字摘录。只提取同一条款内适用对象、要素与显式单位对应的唯一数值；范围、冲突、否定、疑问或下界条款拒绝。这是有界字面事实解析，不是通用语义蕴含模型。
compare支持eq/ne/lt/le/gt/ge；大小比较要求明确一致单位与有限数值。售后阈值实际执行检索→事实→Excel→le比较，保留两来源与matched结果。
Responses核对响应声明model与请求型号，缺失/错误型号拒绝，允许同名日期快照。每线程最多64条脱敏审计，超限明示丢弃数，usage缺失保持未知；该核对不证明第三方网关底层模型身份。

## 解析与检索

切片保留标题路径、页块、行号、Excel行列及OCR区域；复杂文档使用900字/80重叠，决策由实际PageSignal参与。
TXT/Markdown逐行清洗，保留原文件的空行与换行编号；整篇清洗后的行号不得冒充原文件行号。
document读取、公式取证及search最终选中的文件重新核验原文件SHA与路径；不缓存通过状态，不让旧切片或Dense缓存为篡改/缺失来源背书。只校验选中来源，避免每次全库哈希。文件哈希只能证明入库一致性。
PDF标题stack使用声明层级，识别括号/中文章节/数字标题；跨页继承，缺层级与【】启发式告警，金额单位行不误当标题。8份实际PDF冻结金标首次2/8、修复后8/8，首次失败和PDF渲染归档；属于开发审计。
OCR低质量图片完成最多三次增强，选择置信度更高的结果，原始图也作为候选，避免最后一次处理反而覆盖正确文本。
RapidOCR在同一次推理中记录文本四边形及0/180方向分类，结合高窄crop的90°约定推断无EXIF页面方向和轻微倾斜。至少两条可靠文字、方向票≥80%、角度MAD≤2°才估计；混排/空白/证据不足返回undetermined。正向文字行几何排序保留executor-input坐标帧、尺寸与SHA，不把增强图坐标当原图；非多栏布局模型。图片/PDF/Word传播告警，网页支持±360°内单独校正预览，不覆盖原文件。方向置信度不是页面正确概率。
该选择仍是启发式：平均OCR置信度不能证明语义正确，必须用成对gold评测并保留识别原文。
文字质量另由text_quality.py提供OpenCC繁简转换、有限业务错字告警和确认校正预览。BM25与BGE统一繁简，返回引用保持原文；转换版本进入向量缓存key。用户确认的错字只应用于独立预览，来源SHA变化或伪造选项拒绝。数字、人名和未知OCR错字不推测。

BGE模型文件固定revision及SHA，禁止remote code、pickle和隐式联网加载。缓存key同时包含模型revision、标题及内容。
BM25与Dense通过RRF融合，保留各路rank、cosine及score。阈值0.35/0.6属于尚未独立校准的开发门槛。
CASE0005等含数字的明确编号是范围约束，候选必须准确包含编号；CASE00050不满足CASE0005，未知编号返回无证据。
这个守卫用于防止语义向量混淆近似编号，并不等同于通用实体链接已经解决。

## 服务接口

| 接口 | 功能 |
|---|---|
| GET /health | 当前配置与检索模式；不是模型鉴权实时证明 |
| POST /api/v1/omni/query | 多轮统一问数问答 |
| POST /api/v1/omni/clarify | 服务端核验选项及时间回填，保持统一会话 |
| POST /api/v1/nl2sql/query、/clarify | 问数及澄清回填 |
| POST /api/v1/knowledge/ingest、/query | 实际文件入库与回答 |
| GET /api/v1/knowledge/documents/{id}/original | 哈希核验后的原文件 |
| POST /api/v1/fusion/execute | 有界依赖计划执行 |
| POST /api/v1/documents/ocr | 真实OCR与恢复记录 |
| GET /api/v1/capabilities、/specification | 证据清单、保存的API探测、官方PDF |

API对文件大小、工具数、查询长度、行数、文档解析并发、生产Bearer、CORS和超时分别限制。
开发演示绑定127.0.0.1；模型凭据只存在本地runtime配置及服务端内存，不传前端。
完整性错误返回HTTP409，存储异常返回脱敏503及Retry-After: 2。会话存储故障不能自动换成新内存身份；解锁后原历史继续。SQL连接和初始化连接均在scope退出时立即关闭。
SQLite路径用Path.resolve().as_uri()构造只读URI，避免#和%被解释成查询或片段。单次请求的Schema、值索引、日期推断和执行共用读取事务；缓存代际包含主库/WAL元数据、Schema版本和小型值别名文件SHA。source_revision为代际摘要，不是全部数据内容哈希。请求日期缓存namespace使用其固定revision，避免并发线程切换全局缓存造成混用。

## 效果与局限

最终完整本地回归706 passed、0 failed、1条StarletteDeprecationWarning；日期角色、Schema主体覆盖及既有模型门控专项170项通过。合成QA11/11、SQL12/12、跨源五流程5/5，第五类已产出实际比较。公共Chinook开发题12/12，干扰Schema9/9。文字质量开发对照首次13/14→修复扩展15/15，有限词表与原文保留不代表通用纠错。无EXIF方向/倾斜新增契约首次0/10→扩展13/13；不是OCR文字准确率从0提升。差旅Schema首次6/8→8/8、PDF目录首次2/8→8/8；均保留首次失败，是开发回归，不是盲测。单/多指标趋势按时间升序，显式排名按指标排序。实际数据库更新和任务中途变化两个审计均首次1/5→修复5/5，单独保留首次失败。
订阅账单领域由协作代理设计并在首次执行前冻结输入SHA，首轮0/12；通用日期/主题覆盖修复后9/11有效题，12题总尝试，HF11的并列排序金标口径错误单列无效。其余字段与数值分别核对；后测已曝光，是开发回归，不是官方/严格独立盲测，不据这些题添加主题词白名单。
实际临时副本故障审计首次4/10、修复后10/10，含原文件篡改/缺失、SQL缺库/锁、kill子服务后五轮与pending澄清恢复、会话锁503及解锁后历史保持。500页来源校验后全局语义热P50=523.822ms、P95=618.806ms，首次全局5.390s；不含OCR或外部生成，也不是新进程完整冷启动。
十种合成OCR扰动原图8/10、增强10/10；这是本地成对对照，不能外推大规模文档准确率。
指定gpt-6-luna真实预检已HTTP200；44题端到端开发验收首轮11/44、第二轮32/44。SQL内部接真实Responses规划器：19/19执行值正确，但完整路由等契约仅16/19整题通过；文档题8/13通过，4篇摘录回退不计生成通过。83次API调用均completed，网关上报443785 tokens；调用完成不证明语义正确，模型吞吐和完整复杂多跳仍未全面通过。
评测含两组连续五轮、五类跨源、主题切换/重置及澄清回填。首轮保留在REAL_MODEL_FIRST_RUN_20261001.json，第二轮在REAL_MODEL_REPORT.json及字节一致归档REAL_MODEL_SECOND_RUN_20261001.json；第二轮12个未通过项随后定向验收，独立报告REAL_MODEL_TARGETED_REPORT.json为15/19（所选12失败9通过、7前置6通过）。38次API完成，上报195833 tokens，约3分15秒。未重跑全44，不拼接当前全量准确率；上述成绩包含服务端规范化，不是裸模型或官方准确率，未提供第三方单价不估美元费用。

定向SQL连续五轮5/5、客单价两轮2/2、跨源阈值1/1、文档→SQL1/1；预测两轮1/2。尚存4项：qa-07/qa-08自由改写缺乏程序可证明的支持关系而摘录回退；政策版本比较仍选择document路线；预测追问的SQL业务标签与DAG引用路径不一致，安全停止计算。失败保留，摘录不算模型生成通过。HTTP_MODEL_ACCEPTANCE_REPORT.json实际HTTP2/2（SSE销售额29584、保修真实生成引用）及网页人工三源预测33134.08为局部交互证据，不覆盖其余题。
报告数据均在docs/*REPORT.json；MODEL_API_PROBE.json从第二轮保存的真实预检派生为HTTP200，不发起额外调用。MODEL_API_PROBE_INITIAL_20261001.json保留早期401，MODEL_VALIDATION_UPDATE_20261001.md标记准备阶段历史；更早模型证据放history，不混入当前结论。BROWSER_MODEL_ACCEPTANCE_20261001.json保存实际DOM两例，其中三源预测33134.08与SQL29584；这是局部交互证据。

## 复现与后续验收

依赖环境见ict-track8/requirements.txt、requirements-tested.txt和ENVIRONMENT.json；17项直接依赖由现场metadata核对，不是完整传递依赖lock、跨平台wheel清单或安装来源证明。可选Tesseract需另装Python适配器、二进制与语言数据，未在当前环境验收。公开样本、Chinook和BGE分别有授权与SHA清单，完整包包含公开资产但排除runtime及backup。
后续需要严格独立保留题集、未知Schema及完整通过的多跳五轮真实API，扩大政策冲突/单位歧义/坏文件/故障注入。按用户要求只完善程序，不再制作Word/PDF/PPT。
evaluate_model.py支持--workers 3并行独立会话；--case-ids定向选择自动加入同会话全部前置题且不改题序/历史计数；--output只能写docs下新的JSON报告，拒绝覆盖历史；--preview不读凭据/不联网。定向结果显式区分所选与前置题，不能替代全量评测。
交付前必须从新目录验证ZIP逐文件哈希，创建demo库、重新入库、启动本地服务，再实际核对SQL、OCR、Dense与原文件。
程序包新目录验收见压缩包旁.smoke.json；历史方向包29/29，本轮最终包需核对旁报告；本地706测试通过不代替解包验收或洁净依赖环境安装。
