# 复杂原件算术问答缺口定位

只观察生产流程，未改提示、模型输出或审核规则。工具 `tools/probe_original_answer_trace.py` 逐次保存完整原件上下文、schema与首次模型决策。参考答案、正确文档提示、页码提示和旧答案均未进入生产请求。模型固定gpt-6-luna/medium。报告 `ORIGINAL_ARITHMETIC_DECISIONS_ROUND32_20261006.json` 源码起止一致，不是准确率评分。

## 两类资金总额差值

原问：What is the difference between the total FSA funds budgeted and the total non-FSA funds budgeted for U.S. Government assistance to Ukraine in FY 2006?

同一原PDF `ohr-4fcf317d5db06ebb2726` 第1页明确印有 `TOTAL FSA FUNDS BUDGETED: $82.16m` 与 `TOTAL NON-FSA FUNDS BUDGETED: $72.54m`。只读核验原件SHA后重新抽取，生产原生表格事实清单没有这两行；首次原生选择模型及补充检索后的再次选择都abstain。模型收到的表格事实没有完整两个操作数，不能靠放宽审核或改弃答提示解决。

定位 `_currency_panels`：货币金额先按水平坐标形成至少3行的金额列，随后按部门标题和连续行分组，每组仍要求至少3行。两个单独的金额总额标题不满足连续表格的认证范围。不能简单删除最小行数要求，把孤立正文金额冒充完整表格。

下一步应明确区分独立标注金额和表格单元格，分别保留原生标签、字面金额、自身相邻m后缀及边界框。跨标注算术需要单独证明原件、国家、时期、项目角色与单位对应关系，并保留独立语义审核与原件重放；不得借用另一个部门标题或整页通用倍率。

源码冻结期间在 `tools/prototypes/sparse_native_amounts.py` 做了原件标注提取原型，复用生产金额token及倍率解析，不改生产表格最小行数。在两个国家的原件中分别找回82.16m/72.54m和29.03m/14.51m的原标签及原位置。原件报告 `SPARSE_NATIVE_AMOUNTS_PROTOTYPE_ROUND33_20261006.json` 保留全部19个total标注，未只保留符合已知答案的四项；无模型、问题或gold输入。所有标注仍声明非表格、未证明语义关系、不能作计算输入。11项独立原件检查通过，包括相距较远的孤立总额、相同标签不合并、远处后缀不借用、错误SHA/页码拒绝及不支持的旋转拒绝。未接入生产回答，不计准确率提升。

## 跨年利润布尔与差值

原问：Did the Group's profit for the year increase in 2008 compared to 2007, and by how much?

原PDF `ohr-7d7f54c4030047a38a2d` 第14页包含Group和Company两个分组，各自2008/2007列及RM’000。Group的Profit for the year为245,721与100,140；Company对应23,620与804,944。第15页另有USD转换报表，不能把Group/Company或RM/USD混用。

直接重新抽取第14/15页，现有生产原生数值表格均为0 facts。生产路由没有相关已认证行，未调用表格选择；后续普通生成和视觉字面选择都abstain。此处同时缺金融表格解析与“是否变化+变化多少”的复合执行能力。仅扩大检索或把已有布尔阈值模块泛化为任意布尔输出都不足以正确解决。

下一步顺序：先证明多层列头Group/Company→年份→原币种及倍率、括号负值及同名重复行位置，再增加有原始操作数支持的方向性比较与Decimal差值组合；完整问题独立审核必须确认布尔结论与差值共同成立。输出应披露原币种，不能使用参考答案决定是哪张表。

复用检查：对相同已核SHA的原件调用现有PyMuPDF `find_tables(strategy='text', min_words_vertical=3, min_words_horizontal=1)`。第15页可提出含两条Profit for the year的4列候选，数值70,700/30,231保留；第14页提出7列候选，但把Distribution expenses等行标签及部分括号负数拆到不同单元格，Group/Company也没有出现在返回的前两行年份/币种列头中。可以复用它作为几何候选，不能直接以find_tables的字符串矩阵作为已认证财务事实。下一轮优先对候选重新核验原生字词边界、多层表头及负数跨度，而不是另造表格检测器。

本轮金额显示修复不能被称为上述两类问题已经修复。当前RAG准确率评分没有因此提高，80分目标仍未证明。
