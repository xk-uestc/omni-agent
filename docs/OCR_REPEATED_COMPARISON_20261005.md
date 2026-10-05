# OCR候选两轮实测比较

## 结论与范围

本机CPU条件下，关键文字覆盖优先推荐开启方向检测的PaddleOCR；交互速度优先考虑新版RapidOCR，但金额标点必须额外核验；本组倾斜表结构优先考虑MinerU standard。MinerU basic在本组倾斜表格和脏污登机牌上有明确失败，standard也有正文漏改，不直接替换生产引擎。正式8030服务与生产依赖未更换。

独立环境及权重位于`D:\ICT8-OfficialDatasets\ocr-candidates`。四个输入各重复两轮，五条已完成链路共40次识别，另有此前标准版5度单页探索。只有两份独立公开PDF和一张官方示例图片；预算5度/90度是同一页的受控变体。不是40份文档，不是官方评测，不代表完整文字准确率。

## 输入与参考

- OHR-Bench预算原件：`ohr-bench/pdfs/administration/DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf`，第一页1.5倍光栅化，生成5度倾斜和90度旋转变体。
- Paddle官方脏污中英登机牌：<https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/general_ocr_002.png>。人工查看原图，仅选清晰可确认的13项；不以模糊座位/时间作为参考。
- OHR-Bench双栏Ukraine财务原件：`ohr-bench/pdfs/administration/DUDE_7970c3f494834b8f00d66ede05bcf343.pdf`，第一页1.5倍光栅化。人工查看总额、部门名称及金额，共18项。
- 精确PNG哈希及识别全文保存在原始报告；重复运行不重新生成图片。

覆盖指标仅检查选定字段/金额字符串是否出现在全文，忽略空白和大小写，不修正字符。同页重复金额去重；每轮65项、两轮130项。金额在正文出现也可能贡献覆盖，因此覆盖绝不替代表格配对。预算另核验11组表格行标签与金额的实际HTML配对。

## 已完成40次结果

| 链路 | 两轮字段覆盖 | 平均识别/调用秒 | 全文重复稳定性 | 明确失败 |
|---|---:|---:|---|---|
| 当前旧RapidOCR原始引擎 | 128/130 | 5.705 | 四输入两轮一致 | 双栏页Chornobyl名称识别有误 |
| RapidOCR3.9.2 / PP-OCRv6 small | 128/130 | 2.919 | 四输入两轮一致 | 90度预算页`$1,000`逗号识别错 |
| PaddleOCR3.7.0 / PP-OCRv6 medium，整页及行方向开启 | 130/130 | 46.225 | 四输入两轮一致 | 所选字段未发现漏项；未评完整全文/表结构 |
| MinerU4.0.10 basic / ONNX结构解析 | 112/130 | 9.729 | 四输入两轮一致 | 登机牌漏8项，双栏页漏`$154.70`，倾斜预算表错配 |
| MinerU4.0.10 standard / 1.2B GGUF | 112/130 | 35.056 | 四输入两轮一致 | 同样漏登机牌8项及双栏页总额；5度正文有漏改 |

速度口径：三文字引擎在各自进程中复用模型，计单次推理；MinerU每次启动独立CLI，计启动与解析总耗时。各链路顺序执行，减少CPU竞争；模型线程/配置仍不同，不能作严格算法性能排行榜。Paddle关闭MKLDNN，无GPU，结果不代表优化CPU或GPU性能。新版Rapid相对旧版本配置均值约快1.95倍，但旧版中文登机牌第二轮1.169秒，新版1.692秒，不能说每个场景都快。

MinerU basic PNG路径两轮：90度预算11/11配对正确；5度预算0/11。Personnel被合并成`$47,000 $1,000`，Staff Mileage被配`$2,000`，Office Supplies被配`$35,000`。此前扫描PDF路径5度1/11，两者输入路径不同，不能合并为相同成绩。稳定重复错误不代表正确。

MinerU standard PNG路径两轮：5度及90度预算均11/11配对正确，共44/44。此结论限于一个预算页的两种变体，没有证明双栏财务表、跨页或多级表头正确。standard的字段覆盖与basic相同，但表结构明显提升，不能仅依据全文覆盖选择表结构模型。

## 建议

1. 常规文字候选：新版RapidOCR，保留坐标与来源契约；先验收金额标点、旋转、当前恢复流程，暂不直接切生产。
2. 质量复核候选：PaddleOCR开启整页和行方向，适合复杂旋转/文字复核；CPU耗时较高，不宜每页无条件双跑。
3. 表格：MinerU standard可作为复杂表结构复核候选；先方向/倾斜校正，再识别和结构解析，逐行检查标签金额、空白、合计，不把结构模型输出直接当事实。MinerU basic不能单独承担当前金额入库。
4. 用更多独立中文财务扫描、真实手机拍照、跨页与多级表头复核，才能确定生产选型。这组样本不足以称任何OCR世界最强。

## 复现

`tools/ocr_candidate_trial.py PROVIDER --expanded --rounds 2`在对应独立环境运行，PROVIDER为legacy、rapid、paddle-oriented；Paddle使用外置缓存。`tools/ocr_mineru_repeated.py --tier basic --rounds 2`与`--tier standard --rounds 2`分别顺序调用两轮。`tools/score_ocr_repeated.py`生成`D:\ICT8-OfficialDatasets\ocr-candidates\repeated-comparison.json`，保留全文、漏项、来源、SHA和失败。五条链路均无进程失败，漏项与错配仍保留。

本轮MinerU标准版GGUF两权重已下载，大小531066496及709409600字节，本地SHA256分别3f3523429c880d675c0330cb3de78240452bc40a961d037e24f91ab8488f03e7、b6c08ab50352677f7396af45ee60682f353f55488e26911d8154bfc37514d3f8。GLM-OCR未安装未测试。

## 标准版额外核验注意

MinerU标准版首次5度预算表11/11配对正确，但正文出现原件`ODJFS`丢失、`received a grant`与`ultrasounds to clients`缺失及部分内容改写。原PDF文字层可独立核对这些词句。评分脚本额外记录四项原文短语（包括日期），不混入130项字段覆盖。它们也不等于完整CER或全文准确率。即使表格正确，也不能称整页识别无误。

MinerU两档共同的登机牌漏项对应布局中未进入输出文字的区域；这反映完整文档解析链路的失败，不能简单归因其底层OCR字形识别能力。报告评分的是可用输出，而不是只评价识别器内部原始输出。
