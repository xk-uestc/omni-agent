# 快速 OCR 融合：真实运行与验收边界

本轮实现独立的 Rapid 全页检测、Paddle 仅裁剪识别实验。目标是减少旧融合中整页专家推理的成本，同时验证数字召回是否接近完整 Paddle。没有切换正式 8030 服务，也没有修改只读借鉴项目。

**验收结论：明显提速成立，达到 Paddle 数字召回尚不成立，不能宣布两个目标都完成。**

| 新样本批次 | Rapid | 快速融合 | 完整 Paddle | 融合热墙钟 | Paddle 热推理 | 保守速度比 |
|---|---:|---:|---:|---:|---:|---:|
| 第一批，72 项 | 61（84.72%） | 63（87.50%） | 68（94.44%） | 30.93 秒 | 117.68 秒 | 3.80 倍 |
| 第二批，114 项 | 81（71.05%） | 82（71.93%） | 91（79.82%） | 51.20 秒 | 231.30 秒 | 4.52 倍 |

两批为不同冻结版本，不合并宣称同一算法的整体成绩。各批相对 Rapid 回退均为 0；第一批恢复 2 项，第二批恢复 1 项。第二批全部 84 次裁剪的两 reader 均无推理错误。第二版相对 Rapid 仍增加约 3.67 倍热时间，不能称为和 Rapid 同速。

第二批真实点阵浅色收据 `cord-013` 中，Rapid 漏掉多数金额区域，候选路由未补齐；识别器无法修复没有覆盖到的像素。下一步优先复用 Paddle 的局部检测，只对检测缺口区域调用，再用全新数据验收，不能靠放宽共识或改评分阈值宣布精度提高。日期空格合并与数字解析的分歧也应与 OCR 字符错误分别记录。

## 实现

- `ict-track8/backend/ocr_fast_fusion.py`：数字行规划、原图墨迹候选、竖排编号合并、读数保护、输出几何绑定。
- `tools/ocr_fast_fusion_worker.py`：隔离环境中的持久 Rapid/Paddle 进程，裁剪批量识别，按路径恢复输入顺序。
- `tools/ocr_fast_fusion_trial.py`：真实全页 Rapid、新裁剪识别、逐页实际墙钟、冷启动、代码快照与 SHA。
- `tools/ocr_fast_fusion_report.py`：独立完整 Paddle 对照、三类数字匹配指标、原图 SVG 与全部裁剪证据。
- `ict-track8/tests/test_ocr_fast_fusion.py`：来源、格式、单位、预算、定位和工作进程失败保护。

每页先做 Rapid 全页推理，用已有 OCR 几何和原图连通墨迹提出最多 24 块候选；整行复核失败时，最多补 4 个真实 CTC 数字裁剪。Rapid 与 Paddle 在同一实际裁剪上识别。数值不一致、置信度不足或来源哈希不符时保留原结果，不猜答案。正常清晰整数字跳过，日期、单位、分隔符、符号和位数有保护。

窄而高的单字符先从原图获取横向上下文，避免共享裁剪函数把字符 `1` 旋转为横排。新增数字绑定实际识别裁剪的反变换原图范围，另存墨迹提议框，不能把提议框冒充实际读取范围。几何候选不是识别结果，未获两识别器确认不能加入输出。

批量识别失败、缺少输出、输出顺序或 SHA 错误将明确中止实验，不能默默退化为 Rapid 后宣称融合成功。模型冷启动、推理错误与日志单独保留。

## 数据及实验顺序

数据根目录：`D:\ICT8-OfficialDatasets\ocr-candidates`。

1. 开发输入为此前已经看过的 FUNSD 测试集前 8 张和 CORD-v2 测试集行 0～7，共 16 图、222 个官方数字词标注，不再称为盲测。
2. 第一批新验证为 FUNSD 排序索引 8～11 与 CORD 行 8～11，8 图、72 项。目录 `fast-fusion-new-holdout-20261005`。冻结后才推理，结果为 Rapid 61、融合 63、完整 Paddle 68；融合 30.93 秒，Paddle 全页推理 117.68 秒，约 3.80 倍。随后该批转为诊断输入。
3. 从第一批诊断发现细竖排编号筛选及实际裁剪定位问题，修复后再次冻结，第二批改用 FUNSD 索引 12～15 与 CORD 行 12～15。目录 `fast-fusion-final-holdout-20261005`。第二批结果详见该目录 `comparison.json` 和 `report.html`，不得以第一批得分冒充第二版新验证得分。

FUNSD 来自作者官网 `https://guillaumejaume.github.io/FUNSD/dataset.zip`；CORD-v2 来自作者公开仓库的 Hugging Face 数据。图像、官方标注、下载响应、源文件快照和 SHA 均保留。CORD rows 接口提供的是下载时服务结果，标注中的 revision 是公开版本参考，不应声称接口做了 revision 锁定；本地内容身份以 SHA 为准。

公开测试图可能已进入上游模型预训练；无法证明训练无重合。本实验不是比赛官方测试集，也不是完整全文 OCR 排行榜。

## 计分及时间口径

数字召回要求数值相同、预测位置覆盖官方数字词框至少 40%，并进行一对一匹配。额外提供字面数字召回、紧框 IoU≥0.3 数字召回、标注数字范围内精确率、范围外候选数。官方未标注区域不计入精确率，因此该精确率不等于全页误报率。

CORD 的印尼数字格式由全部比较方法共享；不能把数字格式解析贡献全部归因于融合。完整 Paddle 的行框未做字符对齐，紧框指标不能直接用于宣布检测器优劣。裁剪确认仍可能两模型同时识别错误。

融合热时间为逐页实际墙钟，包含规划、原图裁剪、文件 I/O、IPC、两模型裁剪识别和合并；评分在计时后进行。Rapid 时间是融合内共享的全页推理阶段。Paddle 对照是单独运行的完整全页模型推理时间，未含 IPC，速度比偏保守。冷启动另列。每一批仅一次正式计时，不是延迟分布或统计显著性结论。

开发轮也验证了不能假设批量一定快：r2 串行裁剪为 195/222、75.24 秒；r4 批量为 195/222、83.47 秒。二者还存在候选规则变化，不能把差异全部归因于批量。没有在报告中隐去较慢或失败实验。

复用 RapidOCR 3.9.2 的 PP-OCRv6 small ONNX 与 PaddleOCR 的 PP-OCRv6 medium；没有将上游神经模型改名为自研。新增的是候选调度、同源裁剪融合和来源保护，模型、数据的上游许可继续适用。付费 API 调用为 0，CPU、内存与时间成本不为 0。

## 复现

```powershell
python tools/ocr_fast_fusion_trial.py --source <含 manifest.json 的目录> --output <新的不存在的实验目录>
python tools/ocr_fast_fusion_report.py <含 manifest.json 和 fast/result.json 的目录> --run-paddle
python -m pytest ict-track8/tests/test_ocr_fast_fusion.py ict-track8/tests/test_ocr_fusion_validation.py ict-track8/tests/test_ocr_table_fusion.py ict-track8/tests/test_ocr_region_fusion.py ict-track8/tests/test_ocr_fusion_trial.py ict-track8/tests/test_ocr.py -q
```

相关回归测试为 63 passed。此结果不表示整个项目完成验收。保存了实验代码和证据；未执行 Git 提交、push 或正式生产切换。
