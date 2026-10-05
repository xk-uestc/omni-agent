# 独立OCR候选下载与初步实测

候选与独立Python环境位于 `D:\ICT8-OfficialDatasets\ocr-candidates`，未改生产依赖和8030识别器。

已安装并真实运行：PaddleOCR 3.7.0/PaddlePaddle 3.2.1（PP-OCRv6 medium）、RapidOCR 3.9.2（PP-OCRv6 small ONNX）、MinerU 4.0.10 basic（ONNX文字/布局/表结构）。三个环境pip check通过。另尝试MinerU standard的1.2B GGUF文档模型，结果需以最终日志为准；GLM-OCR没有安装或运行，不混称已测。

输入是OHR-Bench公开预算PDF第一页光栅化的0/5/90度三个变体，无文字层PDF由相同PNG生成。三个变体不算三份独立公开文档。固定样本SHA：原页29e1ba0803262266df5cb49b848408a6c4381eb12d9298f7e790b97dc055d263；5度79577eaf2285a370ba4d846b901ae4c1fbe2d50872d552e4c9e0b9eeadf70012；90度d0bc92bcf0d374bd5de3efb0c3996400d2724e6efb95b6f90c5df64af9af6b80。首次新版环境重新生成图片导致序列化SHA变化，已修正为只在prepare显式生成，重新跑新版，最终报告三组SHA一致。

## 初步结果

| 候选 | 0度/5度/90度标签存在数量（各11） | 耗时秒/页 | 表格标签金额配对 |
|---|---|---|---|
| 旧RapidOCR原始识别 | 11/11/11 | 16.1/13.3/12.0 | 此脚本未评估 |
| 新RapidOCR/PP-OCRv6 small | 11/11/11 | 5.0/4.2/3.6 | 此脚本未评估，5度阅读顺序有问题 |
| PaddleOCR/PP-OCRv6 medium，关闭方向检测 | 11/11/1 | 50.8/48.0/47.3 | 此脚本未评估 |
| PaddleOCR，开启整页和行方向检测 | 11/11/11 | 47.8/41.3/44.5 | 此脚本未评估 |
| MinerU basic | 输出结构化表格 | 热缓存推理约6.6/5.6秒（0/90）；5度冷启动约24秒含部分模型下载 | 0度11/11，5度1/11，90度11/11 |

不同进程并行运行，耗时受CPU竞争、线程配置、冷启动影响，不能视为严格速度排名。Paddle关闭MKLDNN以先验证普通CPU路径，不代表其最佳CPU性能。原始文字出现某个金额不证明对应单元格正确；Presence检查不用于准确率声明。特别是全文叙述也含金额，不能用全文$2,000是否存在代替Office Supplies金额核验。

MinerU 5度错位例：Personnel-$1,000、Staff Mileage-$2,000、Office Supplies-$35,000、Other:Diapers-$100,000；原件参考分别$47,000/$1,000/$2,000/$1,000。因此不直接采纳basic的表结构输出。Paddle开启方向检测后90度恢复全部标签，证明配置对于旋转至关重要。

## 可复现产物

- `tools/ocr_candidate_trial.py prepare`固定样本，legacy/rapid/paddle/paddle-oriented分别在对应环境运行。
- `tools/score_ocr_candidates.py`统计标签存在与MinerU实际HTML行标签金额配对，参考11对保存在脚本及comparison.json。
- 原始识别全文：外置目录中的legacy-trial.json、rapid-trial.json、paddle-trial.json、paddle-oriented-trial.json。
- MinerU原始结构：mineru-basic/budget-0.json、budget-5.json、budget-90.json。
- 汇总：外置目录comparison.json。

建议把新版轻量识别器作为候选，在更多中文/真实扫描/复杂表格页和现有原坐标契约上继续验收；不能仅凭一个预算页替换正式引擎。更强文档VLM还要真实跑通才能比较，不拿其公开榜单替代本机结果。
