# 完整模型验收准备与第五类跨源修复

## 实际结果

- 完整本地回归：524 passed，1个Starlette弃用警告，见LOCAL_REGRESSION_REPORT.json。
- Dense验收：QA11/11、SQL12/12、五类跨源5/5，见HYBRID_ACCEPTANCE_REPORT.json。
- 唯一真实gpt-6-luna Responses预检：HTTP401，44题全部未执行，见REAL_MODEL_REPORT.json。
- 本地开发题及API契约stub均不能计为模型准确率或官方成绩。

## 修复

1. 原真实评测仅4题，SQL内部未接真实规划器。现接入ResponsesModelPlanProvider及指标词典/参考日期，44题包含≥10个问数与问答、五类跨源、连续五轮SQL/跨源、主题切换/重置及澄清回填。
2. 原每题只取最后一次API审计。现汇总路由、生成、SQL规划与重试的每次调用。每线程最多64条历史，超限明示；失败和usage缺失保留，不填假0，不估第三方美元费用。
3. 只请求gpt-6-luna；响应缺model/错误model拒绝，允许同名日期快照。网关model是服务端声明，不是底层身份认证。禁止HTTP重定向；401/403不重试，临时网络/429/5xx按配置有界退避。
4. 原第五类只并列展示文档与Excel，没有比较。现search→search_fact→document_cell→compare，真实开发样本为2小时≤2小时、matched=true，前端同步显示符合/不符合及两来源。

## 事实边界

search_fact只接受前步检索引用，并重核SHA、切片、locator、逐字摘录。同条款内定位适用对象、要素及明确单位；来源冲突、多个同单位数值、范围、否定、疑问及下界条款拒绝。不进行任意语义抽取或隐式单位换算。compare大小运算要求明确一致单位和有限数字。

API输出JSON成功不代表SQL/跨源结果正确。规则兜底、SQL规则规划与摘录回答不算真实模型通过。

## 复现

```powershell
python tools/evaluate_model.py --full --preview
python tools/evaluate_model.py --full
python tools/evaluate_independent.py --dense
python tools/evaluate_regression.py
```

preview不读凭据、不联网。真实测试读取本机runtime/model_config.json，Git/ZIP/日志/前端排除。当前401表示网关拒绝此次鉴权，不能取得准确率；本地回归不代表鉴权已解决。

## 未完成项

指定模型实测、独立未知题、模型时间/空间和费用、官方模板、真实初赛反馈尚未完成。文档质量综合评估中的繁体/错字校正完整性仍待专项验收；OCR十种扰动不能替代全部官方要求。
