# 第七轮三项能力补强与实测

本轮代码已覆盖三项改进方向，专项能力有提升，但还不能把它们标记为全面达标。当前开发目录为 `D:\ICT8-OmniAgent`，大型官方数据外置。验收以 RELEASE 报告和 `ROUND7_RELEASE_SOURCE_AUDIT_20261004.json` 为准；文件名含 FINAL 的早期候选不代表最新源码。

## 1. 陌生Schema复杂NL2SQL

- `backend/nl2sql/complex_query.py`：复杂关系查询提案经只读AST、全作用域列解析、真实表/字段/FK和完整复合FK校验，再独立审核整题。常量参数化并按实际输出顺序绑定。最多一次结构修复，语义拒绝或歧义不重试。
- `backend/nl2sql/relational_output_profile.py`：从真实SQL导出物理字段来源；区分COUNT(*)、nullable COUNT和COUNT DISTINCT；条件计数、加权平均、嵌套聚合保留实际关系来源，避免冒充简单指标。
- 字段角色、分组替换、AVG/rate、显式NULL语义已经补强。保留只读执行器的行数、步数和时间预算；完整结果仍需显式artifacts请求。
- 复杂通道只由明确支持它的模型provider调用，不用于绕过跨源required_intent。`model_validated`同时公开审核通道和`typed_v2_intent_verified=False`，模型审核不是确定性语义证明。

SQL开发专项8/8通过。Sakila固定56题从15/56到27/56，其中独立题3/36到13/36、会话轮次12/20到14/20；整段仍0/4。残余18项澄清、3项输出来源失败、8项SqlSafetyError；最后一类只有错误类型记录，不能全归为行数限制。配对存在改善和退步，见审计逐题记录。

## 2. 复杂文档问答

- `native_text_tables.py`保留多行表头、外侧列边界、bbox和同列显式货币/百分数/倍率，支持财务'000/’000/000s记法，不借用其他列单位。
- `native_table_question.py`支持明确跨年同实体差值/ratio与原PDF表头重放，记录审核拒绝的具体检查项。
- `native_formula_pages.py`仅拼接相邻原生PDF页中几何/字体一致、明确未闭合的数学公式；保存SHA、bbox及AST核验。不支持任意扫描、任意下标或猜测续接。
- 多子问拆分、独立整题完整性审核和claim IDs绑定避免部分答案伪装整题完成。401/403停止，不隐藏API失败。

混合专项共11/12；失败`table_difference`的独立审核`unit_scale_and_sign_preserved=False`，保留拒绝而不降低审核门槛。公式专项为真实PDF生产工具测试，不是模型规划成绩。

OHR固定15文档36题：EM6/36不变，token F1从0.309150到0.319136（+0.009986），4题改善、3题退步、29题相同。仅为有资源选择偏差的小样本，不能声称复杂文档准确率明显提升。中间候选EM5/36和F1下降的报告仍保留。

## 3. 陌生领域连续五轮

- `sql_history_scope.py`仅绑定实际成功SQL、参数、原问题、source_revision及完整payload SHA；先用严格规则处理追问，再对允许的简短追问进行改写和独立审核。
- `omni_agent.py`将此路径标记为`model_reviewed_sql_followup`，不声称server_verified。失败轮、记录篡改、来源变化、换主题、文档路线和复杂排除不会误继承查询。
- 审核拒绝返回明确澄清，防止静默转为丢失过滤条件的查询；401/403停止。持久记录重启后复核。

医疗/物流/财务开发夹具15/15、3/3五轮，包含12轮数值验证和3轮预期澄清。原Sakila四段会话仍0/4，未知领域五轮泛化未解决。综合44题43通过，`cross-turn-1`上游约60秒超时计失败，未拼接旧候选44/44。

## 固定源码证据

| 报告 | 结果 |
|---|---|
| LOCAL_REGRESSION_ROUND7_RELEASE_20261004.json | 2965/0，2跳过、1告警、12 subtests |
| REAL_MODEL_CORE_ROUND7_RELEASE_20261004.json | 43/44 |
| ROUND7_NEW_DOMAIN_RELEASE_20261004.json | 15/15，3/3整段 |
| ROUND7_COMPLEX_PROBES_RELEASE_20261004.json | 11/12，SQL8/8 |
| SAKILA_ROUND7_RELEASE_DEVELOPMENT_20261004.json | 27/56，整段0/4 |
| OHR_ROUND7_RELEASE_20261004.json | EM6/36，F1 0.319136 |
| ROUND7_RELEASE_SOURCE_AUDIT_20261004.json | PASS，同一冻结85文件版本 |

全部真实调用限定gpt-6-luna/medium。审计保存执行字节SHA及LF标准化SHA；跨平台换行不声称完全同字节。Sakila问句已曝光用于诊断，参考SQL/答案与未来问题没有进入模型请求，评分器未修改。单次配对无法证明因果或统计显著，不换算官方90%或获奖等级。

本轮另修复ZIP逐块写入与同步SHA计算，避免read_bytes导致内存不足，专项和完整回归通过；未生成新ZIP，也不把历史交付包认作本版。未改前端视觉，8030按原规则模式运行；真实API效果来自独立评测。本轮不写PPT/Word。
