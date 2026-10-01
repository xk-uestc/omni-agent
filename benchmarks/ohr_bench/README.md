# OHR-Bench 冻结pilot清单

此目录的 `MANIFEST.json` 固定官方8498题中的资源受限12题/7原PDF，六类各2题。公开程序包**不包含原PDF、GT或完整官方数据归档**；本机大型资产放在 `D:/ICT8-OfficialDatasets/ohr-bench`。

从项目根目录恢复同一实验：

```powershell
python tools/fetch_ohr_bench.py --restore-frozen
```

该命令使用现有manifest，验证同题ID/顺序、原题行SHA、逐PDF和GT SHA；不重新选题，不覆盖manifest。需要网络访问固定官方HF与代码revision。通过HTTP Range获取所选原PDF，没有验证完整约1.5GB归档SHA，manifest的 `archive_full_sha_verified=false` 不应改成true。

HF数据卡为CC-BY-4.0；源PDF保留原作者/出版者版权与研究用途限制，不能把数据卡许可当成所有原PDF的任意再分发许可。实际原PDF用于检索/生成，GT/evidence/answer只用于后处理评分，不混入输入。

当前有效评分是 `docs/OHR_BENCH_MODEL_VERIFIED_SCORE_20261001.json`：1个实质回答、9个拒答、2个摘录回退，EM=0/12。资源偏置12题pilot不是完整官方排行榜或代表性准确率。完整来源、评分更正、复现命令和限制见 [官方数据工程验收](../../docs/OFFICIAL_DATASET_ACCEPTANCE.md)。
