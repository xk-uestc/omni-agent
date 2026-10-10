# Memory RL 状态

## 当前：F1基础设施完成，等待审核

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
