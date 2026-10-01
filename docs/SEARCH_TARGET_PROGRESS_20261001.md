# 来源检索协议与后置计算目标

## 已完成

完整本地回归 **1733 passed / 0 failed / 1 Starlette warning / 12 subtests passed**，
pytest 66.45 秒，机器报告 SEARCH_TARGET_REGRESSION_20261001.json。
15项后置目标测试另行完成，避免并行开发期间测试发现范围遗漏；其全部已包含在1733完整回归中。

1. search 工具统一规划/执行协议 query + 可选真实 document_id + 可选 PDF 1-based page_no。
   page 必须附文档，错误 ID、非法页、未知键拒绝；实际 hit 的来源/页/SHA 再核验。
   来源限定无命中不会退回全库。SQL→文档查询引用证明保持，不删除document_id来凑成功。
2. 独立后置计算目标：唯一明确SQL来源后出现“计算/核算/求/算 + 年份/真实实体 + 完整目标”时，
   绑定真实文档公式、目标范围和来源ID。目标年份不借入基准SQL；实际最终计算必须消费正确公式与完整SQL祖先。
   未知条件/目标、多个来源或多个目标的歧义继续澄清。
3. 图表路由：缺年份但系列相关时澄清，不能提前转文字回答；解析/预算异常返回结构化 incomplete。
   来源变更错误不吞，时间预算在每页提取后再次检查。
4. 模型提示明确保留独立指标契约 missing/unit/currency/局部filters，未放松实际服务器校验。
   提示变化不等于模型效果证明。

## 真实验收边界

第九轮39/44与官方OHR EM2/12属于80a5828冻结版本，不能据此宣称本文件后续修复全部效果。
SEARCH_TARGET_TARGETED_REPLAY_20261001.json 为独立定向回放，自动包含所有会话前置轮，
不与全44题拼接为新全量成绩；等待终态与源码前后SHA核验。
官方复杂表格、跨列布局与普通实体关系短答案仍不足，全部赛题目标保持未完成。

## 已终止的定向真实回放

SEARCH_TARGET_TARGETED_REPLAY_20261001.json：**7/7**，包含multiple_documents、forecast_three_sources、
cross-turn-1至5；13调用全部completed、0失败/丢失，63659可见tokens，源码前后SHA一致。
均为gpt-6-luna/medium。它们是已曝光开发题的定向回放，不替代新全44题或官方未知题评测。

## 程序包启动验收

D:/ICT8-Backups/ICT8-program-20261001-search-target-v2.zip，476文件、59837550字节。
SHA-256：8154c0d2bfa1b938aae2bc75b66754194b250353983e6f30e3e95a2e1a289756。
D盘全新临时目录解压启动29/29检查PASS；16份样本逐ID/SHA/modality核验。
报告SEARCH_TARGET_PACKAGE_SMOKE_20261001.json。沿用本机预装Python依赖，未验证洁净OS安装；
包内无运行凭据，smoke不调用模型，不替代上述API证据。包生成在本记录与定向报告最终落盘前，
因此完整最新审计材料以活动项目和随后完整恢复备份为准。
