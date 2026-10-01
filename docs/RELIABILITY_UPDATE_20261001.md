# 规划上下文与可核对来源的可靠性更新

本轮只修改E盘独立项目，未修改D盘原项目及迁移基线。真实API没有再次调用，不使用其他模型。

## 实际修复

| 问题 | 修改 | 验证 |
|---|---|---|
| 首100文档截断、每份仅开头3000字、前30行表格 | 按本轮问题检索全部文档，相关切片及表格行优先，显式ID优先 | 第106文档、正文后段、Excel第71行的实际入库及捕获上下文测试 |
| 预览限额与来源信息 | 最多12文档、每份3000字符、正文与表格内容合计24000字符；截断标记；原文单份发送与偏移locator | 预算、无匹配元数据、表格省略行及原切片回查 |
| 短追问失去原主题、模型每轮全表COUNT | 检索追加上轮独立问题；Schema不计row_count | 后段文档追问契约、Schema断言 |
| MIN/MAX被覆盖守卫误拦截 | 聚合选择与cue消费共用定义；只消费当前已选择函数的词；同指标多聚合明确澄清 | 9项实际聚合数值、4项冲突/未知修饰拒绝 |
| 整篇清洗压缩空行，定位不再对应原文件 | TXT/Markdown逐行清洗，保留原始行序 | LF/CRLF/CR三种换行，重新打开原文件核对第9行标题与第11行事实 |

上下文测试使用CapturingPlanner契约stub，仅证明输入证据能到达规划器，不能证明模型理解、跨源规划或回答准确率。
24000字符预算指正文及序列化表格行的内容，元数据/locators及数据库Schema另计；不是整个模型请求的token上限。

## 新Schema实际执行

输入为新建Claims差旅报销表、六条合成记录，未设置领域别名、few-shot或模型。8题分别覆盖SUM、AVG、部门GROUP BY、年份、状态、组合过滤、MIN、MAX。

- 首次实际结果：6/8。MIN/MAX已识别正确，但覆盖守卫拒绝其提示词。
- 修复后实际结果：8/8，每题均走统一sql路由，并与独立金标SQL执行值一致。
- 输入和金标SHA不变：`906cfa7d55d7c8aeb195257f09da70b8ce96cb7712054be435f29911fb98c680`。
- 首次失败永久保存在DOMAIN_TRANSFER_FIRST_RUN.json；当前结果在DOMAIN_TRANSFER_REPORT.json。
- 本轮属于自编新Schema审计。修复后的题属于开发回归，不是独立盲测、公开排行榜或官方比赛成绩。

## 验证与复现

完整本地回归：415 passed，1项Starlette依赖弃用警告。

```powershell
python -m pytest ict-track8/tests -q
python tools/evaluate_domain_transfer.py
python ict-track8/scripts/package_delivery.py --with-public-assets --output dist/ict8-reliability-20261001.zip
python tools/verify_checkpoint.py dist/ict8-reliability-20261001.zip
```

验包工具增加SQL最小/最大值金标比对，以及上传多空行原文后核验来源定位；解压默认使用系统临时目录，避免项目盘容量不足。
包哈希和各项启动结果保存在对应.smoke.json。运行时模型配置排除于Git、包、日志及前端。

## 尚未完成

指定gpt-6-luna此前实际鉴权401：MODEL_API_PROBE.json。当前没有真实模型新成绩，规则回退不算模型通过。独立未知任务、通用模型多跳五轮、模型端到端性能仍待有效API及完整验收。

delivery的Word/PDF/PPT是ade1b60材料快照。本轮源码和实验新增证据在本文件及JSON，不伪造更新其SOURCE_MANIFEST；正式提交前需重生成材料。项目整体不标记全部完成。
