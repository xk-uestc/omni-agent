# 实时数据库与跨源一致性更新

本轮仅修改独立项目程序与工程验收证据，不修改D盘项目、基线备份或参赛材料。

## 实际故障及修复

| 现场场景 | 修复前 | 修复后 |
|---|---|---|
| WAL中新业务值已提交，主库文件未变 | 旧索引忽略华中，误要求澄清 | 主库/WAL代际刷新索引 |
| WAL内日期从秒变为毫秒 | 旧日期推断造成空结果 | 请求固定代际刷新日期缓存 |
| 业务别名文件在线更新 | 仍用旧映射 | 小文件内容SHA参与缓存失效 |
| 合法库名含#和% | URI片段/转义解析导致错误目标 | as_uri正确编码且mode=ro |
| 两步SQL间新订单提交 | 60/3=20，混用不同版本 | 同一快照60/2=30；下次查询见150/3 |
| 公式取证后原文件篡改/移走/重入库 | 继续使用旧公式计算 | 步骤前后重新校验，停止最终计算 |

## 证据

- 完整本地回归549 passed、0 failed；见LOCAL_REGRESSION_REPORT.json。
- 数据库审计首次1/5，修复5/5；见LIVE_SQL_FIRST_RUN.json和LIVE_SQL_REPORT.json。
- 跨源中途变化首次1/5，修复5/5；见FUSION_CONSISTENCY_FIRST_RUN.json和FUSION_CONSISTENCY_REPORT.json。
- BGE/BM25/RRF实际验收：QA11/11、SQL12/12、跨源5/5；见HYBRID_ACCEPTANCE_REPORT.json。
- 实际故障恢复10/10；见FAULT_RECOVERY_REPORT.json。
- 均为开发审计和本地回归，没有外部模型调用，不是官方未知题准确率。

## 契约与代价

单查询读取事务覆盖Schema、值索引、日期推断及SQL。DAG使用线程隔离、可嵌套、懒连接scope，外层结束释放；异常也释放。文档-only不打开业务库。WAL writer可提交，但checkpoint可能等待有界任务结束，不能声称零代价。

SQL provenance中的source_revision是文件代际及Schema版本摘要，不是全表内容哈希。文档source_validation检查所选原文件SHA及逻辑版本，不保证未来不变、不证明事实或语义正确。文件/版本复核是在各检查点进行，不是跨SQLite与文件系统的分布式原子事务。

新目录交付验收增加SQL快照溯源、文档版本状态和两个真实写入/变化审计；运行结果保存到ZIP旁.smoke.json。旧已验包不覆盖。

用户指定gpt-6-luna鉴权仍401，44题未执行；不重复失败凭据请求、不换模型。程序准备和真实模型效果验收分别报告。
