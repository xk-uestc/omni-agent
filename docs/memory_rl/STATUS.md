# Memory RL 状态

## 当前：真实PDF与RAG升级执行中，未启动训练

- 起点及远端02741bc；Linux已合法恢复原PDF并冻结新OHR/OmniDocBench原件与隔离参考。新增可选MinerU4结构后端实际接入HTTP ingest、KnowledgeStore独立结构记录与检索，保留native正文；未审核公式不进计算/生成/Memory。
- OHR新开发18题固定Top4：已有OCR对照LCS .2836→结构补充 .3545，3改善/0退步；ODB开发12页公式区域11/13、LaTeX严格1/13，表格单元格+跨度 .2627；复杂旋转宽表仍失败。实际生成答案not_run。详情 `docs/foundation/REAL_DOCUMENT_IMPLEMENTATION.md` / `REAL_DOCUMENT_RESULTS.md`；正在冻结后保留集与旧功能回归，不能宣布项目全部完成。

- F3本轮单Agent完成正文条件绑定、明确来源内对象关联与受限pipe原生表bundle生产接入，源码冻结/远端SHA `67c4d3963d7078edeb9c1d5f7c58981caa072ffd`。Top-K开发/保留Recall .5754/.5595、完整均10/21、错绑0/3；Top-K+bundle完整16/13，实际answer证据完整15/12，生成答案not_run。保留3题机构语法拒用、3错绑仍在，无证据污染各3/3；不根据保留分数改算法。
- 最终相关934通过/6既有失败/12子测试，F1旧32指标逐题保持，16/185/22记忆结果保持F2、SQL rows/status一致、task_experience16项通过；不是全项目全绿或RL ready。热P50/P95 23.79/103.39→75.15/159.82ms，最大Python查询分配峰值约+3.81MiB。详见 `docs/foundation/F3_IMPLEMENTATION.md`、`F3_RESULTS.md`，PPO/GRPO未启动，main未改。

- 2026-10-11 用户授权F2六检查点；远端fetch与本地均为F1提交1003a19344e3570a229f71e290d6d987101258d9，memory分支。既有未跟踪Qimem/原样保留。
- 检查点1困难集和No Change A基线完成并单独推送；48题/30文档，开发/保留按来源族隔离。baseline Dev Evidence Recall .4325，Retained .4167，二者各6/21可答题完整；不使用保留分数调参。
- 检查点2持久化BM25完成并单独提交：原排序逐题指标保持一致；10/100/500/1000页热查询P50由动态4.55/107.41/715.83/1461.08ms降至4.94/30.79/529.24/1102.45ms。10页略慢；1000页仍约1.1秒。SQLite FTS5探针中文召回不足，不部署。
- 检查点3开发集证据范围对照已单独推送：6题Top-K/现有dossier/受限表链完整证据0/6、4/6、6/6；表链用3–4页完成30行唯一性与必要条件核验，Source SHA绑定并对重复行/来源更新/页预算失败关闭。C多做机构→编号绑定和精确检索，局部时间有增加，未接入生产路由。
- 检查点4显式聚合覆盖目录默认值的AVG/SUM语义修复已推送（7f7d4ba），217项相关测试通过。检查点5协议失败审计与SSE超时资源状态已推送（6608785），离线相关回归50通过/2个FastAPI既有弃用警告。检查点6最终集、16/185/22本地基线及终审记录已完成，950通过/6既有失败/12子测试/25警告；F2整体不是赛题完成或RL ready。已固定production source SHA 6608785ac364d4188948e1e4167cfb21c9da5b3d；停止等待ChatGPT审核。
- 后续独立复核：`d96a2fa`下manifest 189/189 SHA与字节长度匹配；困难集dynamic/persistent BM25 dev与retained指标一致，SQL/跨源fixture回归315 passed。profile提示tokenize/page-scope耗时，未据单次profile改变检索语义；详见`docs/foundation/F2_FOLLOWUP_REPRO_20261011.md`及`docs/foundation/runs/f2-repeat-*-20261011.json`。困难RAG集没有SQL gold或静态/动态记忆因果证据，仍不进入M2。
- F1旧结论仍有效：不得训练；PPO readiness未成立；旧测试7失败及本轮新增失败要逐项披露。
- F3-CP1从`d61ce23dfa32fcb2be01d6b71235f3e48e693aed`开始：困难集阶段损失逐题报告与机器JSON已完成；Frozen exact Top-K scorer parity dev `.4325`、retained `.4167`。字段格式规范化后确认解析内容多数仍在，候选池与Top-4是主要损失；Coverage选择器净增dev约`.004`、retained无增益。六道表格题上来源绑定重评分发现历史A `.0417`含两个跨源单位命中，正确来源 exact宏均值`.0139`；既有历史分数保留。验证48题/30文档manifest、stage replay两split通过，F2 evidence回归4 passed。详见`docs/foundation/F3_FAILURE_ATTRIBUTION.md`。
- F3-CP2开发集对照：BM25必要证据候选Recall@20 `.5595`，最终Recall@4 `.4325`、完整6/21、错绑6/24、污染3/3。更正：按F1既有流程使用`/tmp/f1-bge-venv`实际运行BGE；版本Torch 2.14.1+cu130、Transformers 5.19.0（高于仓库`<5`范围）。BGE将Recall@4升至`.4405`、来源Recall升至`.8571`，但完整6/21、错绑6/24、污染3/3、候选证据Recall@20 `.5595`均无改善，P95 1.397秒，不上线。最多2个子句扩展令14/24题片段变化，宏观证据召回/安全指标持平且P50/P95约翻倍，不上线。详见`docs/foundation/F3_RETRIEVAL_RESULTS.md`及三份逐题JSON。

## F1基础设施最终审核记录

- F1检查点1–7

- 2026-10-10 F1检查点1–7可执行工作完成；仅memory，未训练、未合并main、付费模型调用0。
- 最终评测后端固定a37c0728deacf1bc01a6e1458ac3826a2d570e2e；检查点6证据提交e1a815c2cc57bdfc3789d0282da8bd255dde97c2，最终远端文档提交SHA见交付反馈。
- BGE固定权重已下载/校验并实际离线运行。A/B开发与保留64成对内容评分0变化；来源导航C增加噪声，不进生产；生成答案指标not_run。
- 存储单来源SQL下推、Dense按key读取；声明页/行scope与来源版本拒用；整体危险请求拒绝、单指标alias与结构化失败Trace已验证。
- 新源码16题A10/B14保持，185题A/B各178/185（历史177，仅safe-09整体拒绝改善，原成功0退步）；22题9/22、22/22、22/22保持，0错误晋升。安全变化不是Memory收益。
- 综合相关回归533passed/7failed/12subtests；另SSE故障1passed。失败及旧源码复现保留，不宣称全绿。AVG/SUM语义、协议/来源规划、全库BM25成本、SSE后台取消仍有限。
- 历史M1-C真实模型7/7/8/9只审计未重跑；经验池仍不足以证明Fixed/Oracle选择增益。PPO Readiness未成立，停止等待ChatGPT审核。
- 详见docs/foundation/FOUNDATION_FINAL_REVIEW.md、RUN_COMMANDS.md、runs/FINAL_ARTIFACT_MANIFEST.json。M1–M4目标不改。

## 以下为M1-C及更早阶段历史状态（F1新结果以上方为准）

- M1-C本轮实现、真实实验、回归和报告已完成；固定经验收益未成立，停止等待ChatGPT审核，不进入M2/M3、不合并main。
- 唯一分支memory，起点5a5f14f8c5aeab0aa9ce331da8b628bcd98933e0；最终生产源码91d7633a23956b27710599c933913ac79c6022de；完整结果检查点94a9926de2cee14c014a9c6e93c63112407d7760。最终文档提交完整SHA见交付反馈。
- task_experience独立类型，实际成功工具轨迹+独立整题反馈形成，复用候选/本地审核/撤销/版本历史；当前方法重新供给原Omni Planner，未复放历史图。HTTP/SSE共用默认关闭开关。
- 真实DeepSeek最终16题四组：A7/B7/C8/D9；开发4/5/4/5，保留3/2/4/4。64任务history=0，源码/DB/冻结输入一致。
- 最终池仅1条真实模型形成的SQL→文档经验；公式种子非法JSON、客单价来源范围失败拒绝形成。B/C选择及首context全部相同，不能把1题差异解释成Oracle策略收益。
- 累计96/144次请求额度，95次有回执，第22次中止在途Token未知；已知输入432293、输出35477。已停止调用，Key不输出、不提交。
- 原16题A10/B14、185题两组177/185，pass向量/SQL结果保持旧记录；业务语义22题9/22、22/22、22/22，正向0/12/12，无新增退化。
- 扩大测试412通过3失败；3项在起点SHA快照原样复现，非本轮新增。实际确认B记忆的来源变更、撤销、跨scope拒用3/3通过。
- JSON/DSML协议错误、C/D及客单价来源绑定、safe-09历史P0仍未解决。正式公式生命周期控制为空池，另有非空治理补测；h07安全拒答正确但解释原因不正确。详情M1_C_RESULTS.md。
- PPO Readiness未成立：没有多条实质不同合法经验、没有可归因的Fixed/Oracle差距，当前反馈主要由模型协议/执行缺口决定。没有训练。
- 总报告M1_C_RESULTS.md；设计M1_C_EXPERIENCE_DESIGN.md；命令EXPERIMENT_LOG.md；正式原始记录runs/m1c-formal-final-20261010/。旧冻结文件/历史报告/master不变，QiMem原始资产保持未跟踪。
