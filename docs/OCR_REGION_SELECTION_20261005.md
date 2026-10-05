# OCR区域证据选优

发现与复核：预算页更多小计来自增强尝试，但标签误识增多。先前覆盖数优先只能选择整份候选，导致金额恢复与标签质量存在冲突。

新增独立 OcrEvidenceSelectionAgent。先保留覆盖最多的候选，再逐个区域查找其他最多三次OCR尝试中的唯一等价区域。要求同来源原图、同scope、相同金额顺序、相同行数，以及每一标签/金额框原图IoU至少0.6；多个匹配不随意选择。满足后选择平均标签置信度更高的完整区域，不补词或纠正字符。

每区域保存实际识别尝试、执行图frame、图像变换链及选择依据；原图高亮使用各框真实original_geometry。外层覆盖候选frame不冒充区域frame，兼容root rows同步被选区域及continuity；PDF映射仍基于各框原图位置。前端显示区域识别尝试和选优说明，置信度不等于事实验证。

真实来源OHR-Bench预算第一页，SHA256 b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a。手工查看原页，26条标签转录按小写、字母数字、乘号转x规范化，698个参考字符：上一轮30个字符编辑错误，本轮1个，归一化CER 4.30%→0.14%。剩余Withholding→Witholding已由原页放大裁剪核查，未自动修正。该规范化不计空格/标点错误，不是全页CER或官方成绩。

所选44个金额44/44保持、财务页7/7保持；本轮真实预算13个区域换用了标签置信度更高的尝试。短条目仍有字母误识，高置信度也不能保证全部正确。没有用业务词典篡改OCR。

证据：runtime/public-region-selection-pages-20261005.json、runtime/public-region-selection-values-20261005.json、runtime/public-region-label-reference-20261005.json；原页标签裁剪runtime/budget-parking-label-reference-20261005.png。核验脚本tools/score_public_region_labels.py及tools/score_public_merged_amounts.py。公共运行后新增root continuity同步及frame说明只有最终回归证据，不能冒充重跑公开OCR。

最终相关后端118通过，前端42通过。覆盖来源/金额/位置不一致、非法置信度、重复匹配、输入不修改，以及真实图像frame的流水线组合测试。未重跑最终全量或真实浏览器点击。

整体目标仍未完成：更难模糊/透视原件、跨页/多级表头、机构/单位语义、独立数值核验入库、任意复杂多轮指代与统一实时编排仍需推进。
