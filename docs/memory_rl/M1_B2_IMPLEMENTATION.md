# M1-B2 形成、审核与生命周期

当前实现：`backend/memory/extraction.py`纯结构化候选提取，`formation.py`来源核验/审批/撤销，`admin.py`可信本地CLI；`core.py`迁移v1→v2，保留原memories/events并新增候选、来源事件、候选事件、审核收据、旧版本历史表。迁移在BEGIN IMMEDIATE事务中，不支持版本拒绝；旧Store可继续recall。

`MemoryAdapter.run`在实际文档回答完成并observe存档后，且`formation_enabled`开启时，将真实citation交给capture。capture仅支持txt中有界“业务口径：JSON”原文，核对SHA、实际chunk及行号；SQL成功/用户声称确认不能形成可信条目。缺字段仍保留candidate。来源contract定义的是资料声称，SHA不证明其业务真值。

候选ID由scope、来源/行/chunk、定义/binding、来源与Schema/Catalog版本等内容hash决定；不同事件同内容去重，但保留事件关联。新来源生成新ID，不覆盖旧条目。验证重算digest，核对当前原件与原文位置，复用M1-B1 invalid_reason核查字段/聚合/过滤/时态/版本，再查规则alias和同名候选/可信记忆冲突。通过只成为validated。

审核是本地CLI，不提供HTTP确认端点。不接受operator/session_id声称授权。配置文件必须是当前OS用户所有、普通文件、不可group/world写、非符号链接；记录真实uid/账号/host/配置digest及scope/Store路径。该边界假设当前OS账号与进程可信，不声称多租户认证或抵御同一OS账号恶意改库。

```bash
PYTHONPATH=ict-track8 /tmp/omni-m1a-venv/bin/python -m backend.memory.admin --config /trusted/admin.json list
PYTHONPATH=ict-track8 /tmp/omni-m1a-venv/bin/python -m backend.memory.admin --config /trusted/admin.json validate --candidate cand-... --digest ...
PYTHONPATH=ict-track8 /tmp/omni-m1a-venv/bin/python -m backend.memory.admin --config /trusted/admin.json review --candidate cand-... --digest ... --decision confirm --request-id approval-... --reason '已核对业务口径'
```

配置包含scope、memory_db、database、knowledge_root、database_source、reference_date，可选aliases/catalog；reference_time只用于冻结开发fixture，否则取服务器当前时间。生产形成需`ICT8_MEMORY_ENABLED=1`且`ICT8_MEMORY_FORMATION_ENABLED=1`，均默认关闭。

确认要求先validated，再在写入事务中重新检查当前候选digest/来源/冲突；审批内容包括候选ID/digest、可信操作上下文、来源/验证收据、时间、决定、理由、memory_id。相同request_id同参数幂等返回原收据，不重新激活失效或撤销记忆；不同参数重用ID拒绝。不能原地重审terminal状态；应从新来源版本重新形成和审核。

`revoke`要求当前memory内容digest；`review --supersedes`将新确认与旧条目关联，旧条目标为superseded，保留全部历史。外部原件与SQLite审核事务不能跨系统原子提交；临写前重查，且Recall仍按保存版本fail closed，来源之后改变不会使旧定义变成有效。

此检查点已通过52项记忆测试，含真实Omni读取→形成→审核→新session、Store重建、来源更新、旧digest、撤销后历史追问、冲突、替代版本、Schema变化及v1升级。完整ABC/回归/Profile尚待运行，不以单测声称最终验收完成。
