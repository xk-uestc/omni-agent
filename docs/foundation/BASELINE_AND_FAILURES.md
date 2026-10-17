# F1 基线与失败

历史 M1-C 64个正式目标的独立归档核验通过：A7/B7/C8/D9；冻结文件、源码起止、数据库起止一致，B/C选择及首请求相同。runs/m1c-audit.json 保存报告/配置 SHA。不重跑96次历史模型调用，不继承剩余48次额度。

当前本地真实基线命令与日志：runs/baseline-tests.txt。153 passed、5 failed；其中两项 API 导入失败是本轮测试误指定不存在的 /tmp/f1-api.sqlite，需初始化隔离夹具后重验，并保留该失败日志。其余三个与 M1-C 原日志一致，不能删除断言或改 Gold 得到绿色。

100份文档/300片段真实入库490.560ms。指定单文档的8次BM25查询为19.150/4.264/4.077/3.945/3.977/4.041/3.998/3.970ms。代码审查确认即使单来源也读取全300片段；这支持查询下推，尚不证明全库缓存值得增加。完整数值见 runs/storage-baseline.json；无模型、合成集合，不是公开基准。

| 优先级 | 失败类别 | 证据 / 下一步 |
|---|---|---|
| P0 | Executor / Safety Failure | safe-09 原混合意图被静默部分执行。整体拒绝，合法删除筛选条件保留；HTTP/SSE/低层一致 |
| P1 | Evidence Coverage Failure | Round11 多行记录遗漏；Round38跨页/多来源闭集仍有限。建立有界 scope 与扫描/供给页区别，沿用原生行 compiler |
| P1 | Retrieval Failure | Round11错误来源占位；全chunk召回缺文档层。冻结源/片段相关性评测后比较两级来源候选 |
| P1 | Source Binding Failure | M1-C C动态过滤与D范围未核验。属于执行能力缺口，经验召回不解决，不绕过校验 |
| P1 | Planner Failure | 无模型跨源须澄清；真实模型存在 JSON/DSML 协议错误，不生成修补假成功 |
| P1 | Answer Generation / Verification Failure | 真实任务h07拒答原因错误；supplied evidence完整不能说明原件全覆盖。增加事实范围审计 |
| P2 | Provider / Transport Failure | 历史provider失败不触发补证；SSE超时worker仍可运行。Trace区分传输失败与证据不足 |
| P2 | Executor Failure | 显示alias物理SUM核验旧失败。另两项reset/shortcut测试与当前保守产品行为冲突，先保留而非改Gold |
| P2 | Retrieval Performance | 指定来源全库反序列化、Dense全缓存加载有源码与量测证据。下推来源过滤/按key读向量，保留SHA验证 |

历史 OHR 已暴露开发集可分析，但本轮不打开隔离的正式 Gold。已有 Windows 报告不能当成本 Linux HEAD 成绩。BGE初始权重/依赖缺失；用户随后授权下载固定权重，真实dense成绩以后续运行证据为准。
