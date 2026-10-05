# 公共扫描预算页的单行与双行条目

重复数值列中不足三行的独立区块不再丢弃，保存在 sparse_observations；保留真实标签与金额框，不生成表头、机构归属或缺失值。每个区块声明 is_table=false、cell_values_verified=false，与三行以上区域分开。

只接受至少三个不重叠的同行标签—数值对支持的重复列，最多32个短区块；只有两个金额、标签与金额重叠或行重叠均不据此构造表格。独立候选参与OCR尝试选优，坐标映回原图和PDF；来源重绑定失败时清除旧PDF标注。

前端紫色虚线表示独立条目，文字注明“未确认表格归属”，不计入表格单元格数量。

## 真实原件证据

来源：OHR-Bench finance/DUDE_4ead21606785b8a12a5382c99c98e38c.pdf 第一页，SHA256 b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a。人工查看原页后，以空间区域和金额顺序核对，参考未取自OCR输出。

- 原四区域29个金额全部保持正确。
- 新增九个短区块，十个金额：Avian Influenza、USDA两侧、Defense、Energy、Peace Corps、Commerce两行、Treasury、NSF。
- 所选39个金额39/39正确且具备匹配原图坐标；不是全页完整率，也不是官方盲测分数。
- 原财务原件DUDE_4f3030e6432f07af55229eae15f63174.pdf 七个金额7/7未退步。
- 证据：runtime/public-sparse-pages-20261005.json、runtime/public-sparse-reference-20261005.json；核对脚本tools/score_public_sparse_regions.py。

## 验证与剩余范围

最终相关后端104 passed、1既有弃用警告，全部前端42 passed。覆盖短区块、稀疏独立候选流水线、重叠反例、原图旋转映射、PDF旋转/cropbox及失败重绑定、候选来源不符不高亮。

未重跑修改后的项目全量；上一轮3650 passed属于本轮之前。未做真实浏览器点击。实际原件已有小计融合框和较小标签间距仍未恢复；完整机构/单位语义、跨页表格、透视校正、数值独立核验及结构化入库仍未完成。整体目标保持未完成。
