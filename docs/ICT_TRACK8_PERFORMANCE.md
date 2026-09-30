# 赛题八结构化问数性能基线

测试命令：

```powershell
python ict-track8/eval/bench_scale.py --repo ict-track8 --rows 1000 --pages 10 --repeats 15 --out eval-reports/bench-1000.json --fail-on-error
python ict-track8/eval/bench_scale.py --repo ict-track8 --rows 10000 --pages 100 --repeats 15 --out eval-reports/bench-10000.json --fail-on-error
python ict-track8/eval/bench_scale.py --repo ict-track8 --rows 100000 --pages 500 --repeats 15 --out eval-reports/bench-100000.json --fail-on-error
```

本节表格保留历史基准结果；请以上面的 `bench_scale.py` 命令生成当前机器结果。
本机（Windows、Python 3.12、SQLite、本地磁盘）历史结果：

| 行数 | 次数 | 失败 | P50 | P95 | 最大 |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 20 | 0 | 1.278 ms | 3.468 ms | 3.468 ms |
| 10,000 | 30 | 0 | 3.968 ms | 6.824 ms | 7.085 ms |
| 100,000 | 30 | 0 | 40.056 ms | 67.121 ms | 67.294 ms |

说明：

- 查询包含按地区聚合、客户等级 JOIN 和日期/地区过滤三类路径；数据是可复现的合成基线，不能替代比赛真实数据报告。
- 只读执行器有 2,000,000 VM-step 默认预算和 10,000,000 硬上限；超预算会显式失败，不会无限占用服务。
- 结果不包含网络、模型调用或 OCR 延迟；这些需要在最终部署环境单独测 P50/P95、吞吐和失败率。

## 文档页级分析基线

命令：

```powershell
python ict-track8/eval/bench_scale.py --repo ict-track8 --rows 1000 --pages 10 100 500 --repeats 5 `
  --out eval-reports/ict8-document-analysis.json --fail-on-error
```

该基准覆盖文本质量、目录、公式和页级 OCR 重试计划，不执行 OCR，不包含网络或模型
延迟。输出必须保留 `failures`、P50/P95、最大耗时和进入重试计划的页数；不能把这组
本地基线当作真实 OCR 吞吐承诺。

OCR A/B 另由 `eval/ocr_eval.py` 执行。若 OCR 执行器不可用，会写出
`{"status":"skipped"}` 报告而不是伪造成功；即使执行器可发现，仍必须以
`pipeline_ok_rate` 和 CER 判断识别质量，工具存在不等于识别通过。

最近一次本机实测（Windows、Python 3.12、5 次迭代，2026-09-23）：

| 页数 | 失败 | P50 | P95 | 最大 | 进入重试计划页数 |
|---:|---:|---:|---:|---:|---:|
| 10 | 0 | 0.234 ms | 0.236 ms | 0.284 ms | 0 |
| 100 | 0 | 2.170 ms | 2.180 ms | 2.464 ms | 13 |
| 500 | 0 | 11.478 ms | 11.502 ms | 11.516 ms | 74 |

该表只证明本地分析链路的规模趋势；外部 OCR 执行、模型推理和生产网络必须在目标
部署环境重新测量。

## 独立 vnext 包 HTTP 冒烟基准

2026-09-30 从独立后端 ZIP 解压后，在本机启动 Uvicorn，使用 Bearer
鉴权连续发送 20 次相同 `/retrieve` 请求。服务先完成一次索引初始化，随后统计
稳定请求耗时：

| 请求数 | 未授权结果 | 已授权结果 | P50 | P95 | 最大 |
|---:|---:|---:|---:|---:|---:|
| 20 | HTTP 401 | 20 × HTTP 200 | 27.5 ms | 40.0 ms | 2,275.9 ms |

最大值来自首次加载/缓存初始化；该结果是本机独立包基准，不代表公网、模型上游
或正式 8014 部署环境的 SLA。目标环境仍需按相同方法复测网络、模型和并发吞吐。
