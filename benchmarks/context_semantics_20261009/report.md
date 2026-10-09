# NL2SQL 通用字段理解升级：最终验收

日期：2026-10-09。项目：D:/ICT8-OmniAgent。已部署至 [公网演示](https://raysource.cloud/demo/)。

## 结论

Agent 已结合真实 Schema、业务指标口径和可信历史上下文识别口语表达及不同字段，覆盖销售、采购、物流、薪酬、回款、费用和网站数据。明确区分总额/单件、整体/组成、毛利/净利润以及实际/目标/归因收入；不能唯一确定时先澄清。字段确认后保持原时间、地区和其余条件，不把短回复变成无范围查询。

本轮开发验收通过；不将自建数据成绩称为官方盲测、国家一等奖或赛题整体完成。方法和迁移边界见 [实现说明](../../docs/SCHEMA_FIELD_UNDERSTANDING_20261009.md)。

## 正确性与旧能力

| 验收 | 结果 | 原始证据 |
|---|---:|---|
| 同一固定 185 回合 | 88/185 → 185/185 | baseline-audit.json、candidate-final.json |
| 原成功题倒退 | 0/88 | candidate-final.json 的 comparison |
| 相关后端回归（69 个文件） | 2121 passed、2 skipped、0 failed | regression-final.xml |
| 回归期间源码和测试稳定 | 是 | regression-final-stability.json |
| 定义/统计/排名/口径独立边界 | 21/21 | routing-confirmed-final.json |
| 字段澄清、原筛选、主题切换、隔离 | 22/22 | clarification-confirmed.json |
| 前端单元格来源高亮 | 7/7 | node --test ict-track8/frontend/source-evidence.test.js |
| API 安全专项 | 5/5 | python -m pytest -q tests/test_api_security.py |
| 部署后本地真实 HTTP | 32/32 | live-http-local.json |
| 部署后公网完整复测 | 32/32 | live-http-public-confirmed.json |

相关测试和专项有重叠，不直接相加。预期安全拒绝/澄清按预期判定通过，不包装成实质答案准确率。资料定义审计验证正确路由和未执行 SQL；资料不足仍为 insufficient_evidence，不算召回成功。

固定题集分项：

| 类别 | 修改前 | 修改后 |
|---|---:|---:|
| ambiguity | 4/7 | 7/7 |
| colloquial | 10/36 | 36/36 |
| domain_colloquial | 1/25 | 25/25 |
| history | 30/52 | 52/52 |
| legacy | 27/31 | 31/31 |
| literal_scope | 1/1 | 1/1 |
| missing_or_safety | 4/10 | 10/10 |
| reset | 3/3 | 3/3 |
| schema_transfer | 0/12 | 12/12 |
| topic_switch | 8/8 | 8/8 |

输入 SHA256：`9fbeca112d7ae5d3280e681c26f023c16b42cc3af9ad963551f05aecd3269afa`。五个数据库修改前/后及两版之间文件哈希均一致；固定题集运行期间 backend 源码稳定。参考 SQL 只进入评分器，独立检查的 188 条参考 SQL 全部可以重放，见 input-gold-audit.json。

## 本地速度

相同机器、相同题目，结束并发测试后先测旧版，再测新版。固定 12 题，每题 10 次串行重复，使用高精度 perf_counter；计时包含完整 OmniAgent.query，不包含预热和独立评分器。只比较两版每次均正确的 11 题，共每版 110 次；旧版另一个题的 10 次失败不作为提速收益。

| 指标 | 修改前 | 修改后 | 耗时减少 |
|---|---:|---:|---:|
| 中位 | 308.140 ms | 228.543 ms | 25.8% |
| P95 | 317.185 ms | 241.377 ms | 23.9% |

11 道共同正确题的单题中位和 P95 均未观察到退化。证据：latency-baseline-confirmed.json、latency-candidate-confirmed.json。两版源码及数据库在测速期间均稳定。初次较低分辨率 monotonic 测量保留为 latency-baseline-final.json，不用于此比较。

新增概念图和追问语法不增加 API 调用；缓存 Schema/别名/归一化，实体值匹配先排除未出现的词。此结论限定本地 NL2SQL；公网网络、外部模型及复杂 RAG 不包含在此延迟比较中。

## 部署与公网观察

8031 进程已更新为 22856，继续以 --with-model 启动；运行目录源码与最终 backend 快照一致。本地/公网首页及 health 均 HTTP 200，见 deployment-final.json。保留原前端和来源展示，未更换现有模型配置。原有公网 supervisor 持续运行。

公网首次 32 回合为 31/32：第 13 回合发生一次 HTTPError，没有可核验响应。首次记录未保存 HTTP 状态码，不能推断具体错误码或根因。该问题随后独立复测 3/3，再完整复测 32/32，全部匹配运行库的独立 SQL。首次失败见 live-http-public.json；重放见 public-failed-question-replay.json。公网瞬时 HTTP 错误仍有实际观察，不能用后续成功覆盖这一记录。

本地和公网验收使用浏览器 User-Agent。默认 Python urllib User-Agent 曾被 Cloudflare 返回 403/1010；浏览器 User-Agent 的首页/health 为 200，不将此误判为应用停止。

## 追踪与边界

- 中途候选、失败审计和旧评分器结果全部保留。合法 CTE、排名包装的来源行集合验证曾出现评分假阴性；受限评分器修订没有改变固定 185 输入或 gold。
- 补充澄清新版 22 回合对已有历史的两个问题明确加“换个主题”，输入独立冻结；不冒称与原探索 22 回合同规格。原探索保留为 clarification-audit-final.json，新输入见 clarification-cases-explicit-new-topic.json。
- 早期旧测试契约问题已在旧实现复现或解释：mock 未接受已有 progress_callback/with_audit 接口、旧单位 unknown 与原 Catalog CNY 不符、旧强制 model_validated 与已有本地核验路径不符。最终检查真实范围、字段、公式、数值和额外模型调用，不通过放松 gold 获得成绩。
- 匿名字段的业务意义必须有别名/指标定义，不能从 x17 一类无说明名称猜业务。任意新领域语言、分布式会话锁、外部 API 或复杂 RAG 全量验收不属于本报告。
- 本轮未覆盖和回滚此前其他修改；完整赛题验收仍由 docs/ACCEPTANCE.md 持续追踪。
