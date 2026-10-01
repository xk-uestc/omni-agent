# 公开数据工程接入与效果边界

当前完成的是固定来源数据接入、可追溯执行及小规模开发/pilot实测，**尚无官方未知题准确率达标证明**。原始报告保留，不修金标凑通过，不把拒答、空结果或截断当作完整能力。

| 数据 | 实测 | 判断 |
|---|---|---|
| Chinook第二轮严格模型 | 12/12，12次API均completed，48197 tokens | 10个非空人工开发题与2个空结果边界；非官方标准题集 |
| AdventureWorks官方CSV三表SQLite移植第二轮 | 4/6，不调用模型或领域词典 | 两题19119组被100行上限截断；非原生PostgreSQL/SQL Server |
| OHR-Bench官方原题资源受限pilot | 12题/7原PDF；1实质回答、9拒答、2摘录回退；EM0/12 | 六类各2题，资源偏置抽样，非完整8498题官方榜单 |

## Chinook

证据：`CHINOOK_MODEL_SECOND_RUN_20261001.json`，首轮 `CHINOOK_MODEL_FIRST_RUN_20261001.json` 原样保留。首轮执行结果12/12一致，但严格模型仅6/12，另6题因指标单位/币种验证进入规则回退，不能称首轮模型12/12。

本轮固定原库的412张Invoice日期为2021-01-01至2025-12-22。C05问2009得到SUM=NULL，C06问2010月分组得到空表；这是正确空结果边界，不应作为两个非空业务答案。只有10题证明非空执行值/分组。C07按无序多重集判分，不证明排名顺序。题是人工开发题，不是Chinook官方题或Spider题。最新路由补丁晚于第二轮模型实测，不据此声称已复测该补丁。

```powershell
python tools/fetch_public_assets.py
python tools/evaluate_chinook.py --model --output docs/CHINOOK_MODEL_NEW_REPLAY.json
```

## AdventureWorks

固定微软 `sql-server-samples` commit：`1346b94fab23bfa7edfdb32afae52282ddc3bfaa`，MIT。数据外置 `D:/ICT8-OfficialDatasets/adventureworks`；仓库保留 `benchmarks/adventureworks/MANIFEST.json`、下载/转换与评测工具。

当前只移植SalesTerritory、Customer、SalesOrderHeader三个表至SQLite。保留原字段、计算列值和内部外键，明确省略指向子集外表的外键；Money转换为SQLite NUMERIC后是二进制浮点，原Decimal精确合计保留用于审计。未支持原默认值/trigger/非主键索引/customXML/geography/procedure；所选表没有复合主键，不能以此证明复合键兼容或原生数据库驱动完成。

`ADVENTUREWORKS_SQLITE_PORT_SECOND_RUN_20261001.json` 中aw01/04/05/06通过，aw02/03分别按客户账号分组金额/订单计数：金标19119组，实际100行。字段、聚合正确仍是整题失败，分母不改。SQL接口新增row_limit/result_completeness和达到上限的提示，只防止静默冒充全量，**没有实现完整分页/导出**。

```powershell
python tools/fetch_adventureworks.py
python tools/evaluate_adventureworks.py --output docs/ADVENTUREWORKS_NEW_REPLAY.json
```

## OHR-Bench

固定HF revision `7f833e3eda9a571a9ea545a8f6d476fa1685033d` 与代码revision `1f421eb428f9f5b8ac0bc8064d6ad1f13fab7af7`。8498个官方原题中按PDF大小/ID优先选择12题/7原PDF，text/table/formula/chart/reading_order/multi各2题；题目、金标与官方原始行一致，选择SHA为 `447b3b7a542b79ca41af4bd6eca23c26849b4785e0bda6659be966b1512e5b45`。该资源受限抽样偏向较小文件、仅7候选文档，不能称代表性泛化通过。

大型资产外置 `D:/ICT8-OfficialDatasets/ohr-bench`。公开程序包只保留冻结manifest、工具和报告，不包含原PDF/GT。HF数据卡为CC-BY-4.0，原PDF保留各自版权与研究用途限制。仅HTTP Range获取所选PDF，逐PDF/GT哈希验证；未验证完整约1.5GB归档SHA，manifest的archive_full_sha_verified=false必须保留。

实际原PDF入库；GT/evidence/answer只用于后处理评分，不进检索或生成。官方页0-based映射为项目页1-based；全页渲染OCR是独立诊断，不按gold页挑选，不用诊断文本替换原PDF输入。复用store必须恰为7原文件且逐SHA一致。

### 正确评分与原始诊断

当前应引用 `OHR_BENCH_MODEL_VERIFIED_SCORE_20261001.json`，对应源 `OHR_BENCH_MODEL_FIRST_20261001.json`。15次API均completed、12845 tokens。10个model_grounded只表示经过该路径，其中1个实质回答、9个insufficient_evidence拒答；另2个extractive_fallback。**EM=0/12，含回退token F1=0.025641**。

按正确gold文档/页过滤实际top4结果、join原evidence后，evidence LCS为BM25=0.291396、Hybrid=0.233342；all-evidence-pages页代理命中BM25=11/12、Hybrid=9/12。它们不是答案准确率。当前实际embedding为中文BGE-small-zh，非官方bge-m3配置，英文OHR效果不足需要单独优化。

原首次LCS诊断错误地使用全部hits和stringified evidence数组；原报告保留，VERIFIED_SCORE只对已保存结果离线更正，没有再次API/检索。源报告SHA `ee4c0ba125c3fbd4461e6a800f20182469142d569a106d4957ccdcdcadd2bdbe`、全部12条分数与冻结资产已独立核对一致。

### 按冻结manifest恢复

```powershell
python tools/fetch_ohr_bench.py --restore-frozen
python tools/evaluate_ohr_bench.py --output docs/OHR_BENCH_LOCAL_NEW_REPLAY.json
python tools/evaluate_ohr_bench.py --with-model --output docs/OHR_BENCH_MODEL_NEW_REPLAY.json
python tools/score_ohr_bench_results.py docs/OHR_BENCH_MODEL_NEW_REPLAY.json --output docs/OHR_BENCH_SCORE_NEW_REPLAY.json
```

`--restore-frozen`按现有manifest恢复同ID、顺序、原题行SHA、PDF/GT SHA，不重新抽题或覆盖manifest。新报告使用新文件名，不覆盖历史。需要联网下载和安装项目依赖；程序包不含大型外置资产，不能描述成离线全量官方数据包。真实调用仍只允许gpt-6-luna/medium，凭据保留本地runtime并排除Git/ZIP。
