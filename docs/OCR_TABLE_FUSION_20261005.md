# 开源表格组件融合候选：2026-10-05

## 实现与复用

- `ict-track8/backend/ocr_table_fusion.py`：金额/单位分离、CTC 位置提取、候选结构匹配、可追溯行标签、有限复核与页质量路由。独立于正式 `ocr.py`。
- `tools/ocr_table_fusion_worker.py`：RapidOCR 3.9.2、RapidTable 3.0.2 / SLANet-plus、img2table 2.0.0、PaddleOCR 3.7.0 / SLANet_plus 的真实推理适配。RapidOCR 路径加载器保持其 BGR 约定，避免把 PIL RGB 数组当 BGR。
- `tools/ocr_table_fusion_trial.py`：实际模型调用、缓存身份核对、局部图像读取、统一评分和交互原图报告。
- `tools/ocr_table_fusion_holdout.py`：两份此前未参与本轮路由调试的公开文档，各清晰/退化视图。
- `tools/ocr_table_fusion_summary.py`：合并两批结果，保留局部融合失败及质量路由候选结果。
- `ict-track8/tests/test_ocr_table_fusion.py`：13 项单测；与前两版融合及现有 OCR 测试合计 45 passed。

上游链接：[RapidTable](https://github.com/RapidAI/RapidTable)、[RapidOCR](https://github.com/RapidAI/RapidOCR)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)（Apache-2.0）；[img2table](https://github.com/xavctn/img2table)、[quantulum3](https://github.com/nielstron/quantulum3)（MIT）。这是依赖复用，未把上游源码改名为自研。

quantulum3 0.10.0 真实解析 `$43.54m` 为 `dollar metre`，因此不采用其短金额单位解析；原始 probe 保留在 worker JSON。短 `m` 没有明确 millions 语境时不换算。Docling 没有安装或运行；MinerU 沿用之前试验结论，没有混称本轮新调用。

## 结果

两批 464 个参考单元格；两张旧预算回归页不进入该分母。第一批含两份公开 PDF 和自建中文表的清晰/退化输入；第二批为另外两份公开 PDF 的四个输入。PDF 原生转图及人工退化不等于真实扫描或拍照。

| 方案 | 原严格解析 | 所有方案共享金额解析 | 共享解析匹配率 |
|---|---:|---:|---:|
| 原始 Rapid | 376/464 | 393/464 | 84.70% |
| 全页 Paddle | 397/464 | 412/464 | 88.79% |
| 上轮局部融合 | 380/464 | 394/464 | 84.91% |
| 本轮局部表格融合 | 386/464 | 400/464 | 86.21% |
| 加页质量路由的候选 | 409/464 | 423/464 | 91.16% |

第一批共享解析：226→229/244；第二批冻结局部方案迁移：168→171/220，落后于 Paddle 的 194/220。发现该失败后新增质量路由：两张密集低清页选择全页 Paddle，该组恢复至 194/220。路由使用低置信数值比例、可靠数值数量等输出特征，不读取答案、样本名或评分；它是看到迁移失败后新增的开发策略，不能将最后结果称为盲测。

原严格口径与共享解析口径分列；解析增加不能全算模型提升。指标是参考数值与原图位置重叠≥0.4 的一对一匹配率，不是全部候选精确率、全文准确率或完整表结构准确率。第一批两张预算回归仍各 17/17 字段。局部方案两批各新增 3 个匹配，所检查参考单元格未发现回退；不代表所有字符或候选均没有误改。

## 机制边界

1. RapidTable 与 Paddle SLANet 是同一模型族，不算两条独立结构证据。与 img2table 的几何结果空间相符才标记跨模型族候选支持；不等于正确率证明。
2. 字符子框来自 RapidOCR CTC 对齐，不是独立检测器或人工真值；不按字符串长度伪造子框。文字内金额保留行标签、单位原文和量纲待确认状态。
3. 改变合法数值需要同一裁剪图上的不同读取链路和观察到的同行格式支持。读数一致仍可能一起读错；数字不由合计反推。
4. 页质量选择只是一项启发式候选。两个引擎的置信度未校准，需新的文档验证误切换率；未证明全局优势或学术首创。

## 证据与复现

产物：

- `D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-20261005-final-live\table-fusion-result.json`
- `D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-transfer-20261005\table-fusion-result.json`
- `D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-summary-20261005.html`

首次尝试、复核版本及源结果保留；覆盖输出前的指标与 provenance 写入 `run-history.jsonl`。结果有输入 SHA、来源 manifest/result SHA、实现 SHA、包版本、缓存标志、原图坐标、裁剪读数与冲突。每个 owned worker 有超时和退出记录。

```powershell
python tools/ocr_table_fusion_trial.py --output D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-20261005-final-live
python tools/ocr_table_fusion_trial.py --source D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-transfer-input-20261005 --output D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-transfer-20261005
python tools/ocr_table_fusion_summary.py
python -m pytest ict-track8/tests/test_ocr_table_fusion.py ict-track8/tests/test_ocr_region_fusion.py ict-track8/tests/test_ocr_fusion_trial.py ict-track8/tests/test_ocr.py -q
```

最终第一批新的结构/字符定位及局部读取调用实际重跑，新增墙钟 42.419 秒。第一阶段 OCR、既有接受决策及整页专家输出复用之前相同输入的真实结果；不称完整端到端耗时。第二批首次新增结构/局部调用 48.899 秒，后续策略调整复放；之前全页 Paddle 实际墙钟 263.612 秒，其费用未计入策略复放耗时。不能用几秒复放声称在线链路更快。未调用付费 API，未修改正式 8030 OCR 入口，未 push Git。
