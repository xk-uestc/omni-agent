# 2026-10-05 程序同步验收材料

这些报告来自本机实际执行，保存原始结果；内部路径反映运行位置，克隆后不要求存在对应路径。都是开发验收，不是官方盲测成绩。

- `excel-parser-baseline.json`：Git HEAD原parse_xlsx方法，26种合成Excel同口径13/26。
- `excel-parser-final.json`：本轮最终读取器26/26，含受检源码SHA。
- `excel-http-report.json`：两次独立服务进程，108/108，模型调用0。
- `excel-browser-report.json`：真实Edge隔离服务29/29，含实际点击入库，没有向生产资料库导入样本。
- `dialogue-http-report.json`：真实gpt-6-luna／medium，44/44行为符合预期，35成功、9预期澄清或拒绝。
- `dialogue-physical-output-audit.json`：28次SQL结果按物理字段角色与独立SQLite对照一致。

完整工程回归结果见 `docs/EXCEL_IRREGULAR_LAYOUT_20261005.md`：4360通过、3跳过、12子测试通过、1既有警告；前端93通过。样本在 `samples/excel-irregular-20261005`，含每文件哈希与已知答案。大型数据和真实凭据不随这些报告发布。
