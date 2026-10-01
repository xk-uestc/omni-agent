# 独立智能体的实现与验收边界

## 用户问题到结果

统一入口/api/v1/omni/query接收question/session_id，先读取最多五轮结构化状态。
启用指定模型时，Responses规划器选择SQL、document、fusion或clarify；输出仅为有界工具计划。
没有有效模型时保留rules_basic/rules_fallback标签，跨源问题需用户在工作台明确工具步骤。
SQL规划不能执行模型提供的裸SQL；所有计划进入同一指标、Schema、覆盖、JOIN粒度及只读安全门。

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
search_fact引用前步search完整结果，重核原文件SHA、chunk、locator和逐字摘录。只提取同一条款内适用对象、要素与显式单位对应的唯一数值；范围、冲突、否定、疑问或下界条款拒绝。这是有界字面事实解析，不是通用语义蕴含模型。
compare支持eq/ne/lt/le/gt/ge；大小比较要求明确一致单位与有限数值。售后阈值实际执行检索→事实→Excel→le比较，保留两来源与matched结果。
Responses核对响应声明model与请求型号，缺失/错误型号拒绝，允许同名日期快照。每线程最多64条脱敏审计，超限明示丢弃数，usage缺失保持未知；该核对不证明第三方网关底层模型身份。

## 解析与检索

切片保留标题路径、页块、行号、Excel行列及OCR区域；复杂文档使用900字/80重叠，决策由实际PageSignal参与。
TXT/Markdown逐行清洗，保留原文件的空行与换行编号；整篇清洗后的行号不得冒充原文件行号。
document读取、公式取证及search最终选中的文件重新核验原文件SHA与路径；不缓存通过状态，不让旧切片或Dense缓存为篡改/缺失来源背书。只校验选中来源，避免每次全库哈希。文件哈希只能证明入库一致性。
PDF标题stack使用声明层级，识别括号/中文章节/数字标题；跨页继承，缺层级与【】启发式告警，金额单位行不误当标题。8份实际PDF冻结金标首次2/8、修复后8/8，首次失败和PDF渲染归档；属于开发审计。
OCR低质量图片完成最多三次增强，选择置信度更高的结果，原始图也作为候选，避免最后一次处理反而覆盖正确文本。
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

## 效果与局限

最近本地回归536项通过（2026-10-01）。合成QA11/11、SQL12/12、跨源五流程5/5，第五类已产出实际比较。公共Chinook开发题12/12，干扰Schema9/9。文字质量开发对照首次13/14→修复扩展15/15，有限词表与原文保留不代表通用纠错。差旅Schema首次6/8→8/8、PDF目录首次2/8→8/8；均保留首次失败，是开发回归，不是盲测。单/多指标趋势按时间升序，显式排名按指标排序。
实际临时副本故障审计首次4/10、修复后10/10，含原文件篡改/缺失、SQL缺库/锁、kill子服务后五轮与pending澄清恢复、会话锁503及解锁后历史保持。500页来源校验后全局语义热P50=523.822ms、P95=618.806ms，首次全局5.390s；不含OCR或外部生成，也不是新进程完整冷启动。
十种合成OCR扰动原图8/10、增强10/10；这是本地成对对照，不能外推大规模文档准确率。
指定gpt-6-luna目前真实请求401，因此通用自然语言多源规划、模型语义忠实度、模型吞吐与成本尚不能宣布通过。
真实评测已准备44题，含两组连续五轮、五类跨源、主题切换/重置及澄清回填；SQL内部也接真实Responses规划器。单次预检401后题目全部未执行，见REAL_MODEL_REPORT.json。
报告数据均在docs/*REPORT.json；旧模型证据放history，不混入当前模型结论。

## 复现与后续验收

依赖环境见requirements；公开样本、Chinook和BGE分别有授权与SHA清单，完整包包含公开资产但排除runtime及backup。
后续需要独立保留题集、未知Schema及多跳五轮真实API，扩大政策冲突/单位歧义/坏文件/故障注入，并制作最终Word/PDF/PPT。
交付前必须从新目录验证ZIP逐文件哈希，创建demo库、重新入库、启动本地服务，再实际核对SQL、OCR、Dense与原文件。
