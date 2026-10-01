# AdventureWorks 官方 CSV 的 SQLite 移植审计

这是微软官方原始 CSV 的三表 SQLite 移植与自行设计问题审计，不是原生 AdventureWorks PostgreSQL 验收，也不是赛题官方题目成绩。

## 来源与实际资产

- 官方仓库：<https://github.com/microsoft/sql-server-samples>，固定提交 `1346b94fab23bfa7edfdb32afae52282ddc3bfaa`。
- 路径：`samples/databases/adventure-works/oltp-install-script/`；MIT 许可证 `license.txt` 一同保存。
- 原始文件与完整 SHA-256 清单位于 `D:/ICT8-OfficialDatasets/adventureworks/original` 和旁边 `MANIFEST.json`。
- SalesTerritory 10 行、Customer 19,820 行、SalesOrderHeader 31,465 行。原始 CSV/DDL/许可证合计约 9.95 MB，SQLite 9,420,800 字节。项目目录只保留小脚本、冻结题目与报告。

## 转换边界

字段顺序、类型、主键和外键从原始 `instawdb.sql` 核对；CSV 没有标题行，使用 UTF-8、制表符分隔。原始 CSV **包含计算列**，Customer.AccountNumber、SalesOrderHeader.SalesOrderNumber 和 TotalDue 被保留并逐行按官方表达式验证。

三表的单列主键及三条内部外键被保留；未下载的 Person、Store、Address 等表对应外键在清单中逐项标记遗漏（共九条）。没有选取复合主键表，不能据此宣称复合键已实测。原生 schema Sales 被摊平为 SQLite 表名；identity、默认值、触发器、非主键索引、XML、geography 和自定义类型等未复现。

money 原始值经 Decimal 检查最多四位小数，精确原始合计保存在清单中。SQLite NUMERIC 仍可能使用二进制浮点存储，SQL 数值比较容差为绝对 0.0001、相对 1e-10。日期字符串保留并验证；UUID 保存为 TEXT。该转换不具有 PostgreSQL 原生 money/decimal/date 全部语义。

## 首次实际结果

6 条多表问题在首次执行前冻结，题目 SHA 见本目录 MANIFEST；规则引擎首测 **0/6**，没有模型 API、领域别名或 few-shot。5 条被同名 CustomerID/AccountNumber 的表字段绑定歧义拦截，另一条“对应”未被语义覆盖守卫消费。系统没有执行猜测 SQL；失败完整保存在 `docs/ADVENTUREWORKS_SQLITE_PORT_FIRST_RUN_20261001.json`，不得把后续开发回归称首次盲测。

默认 100 行安全上限保持原样；大量客户分组即使前 100 行正确，也不应算完整结果通过。报告只展示前 20 行及全体行 SHA，但比较使用完整结果集合。无序多重集核对没有评估展示排名顺序。

通用 Schema 字段资格解析修复后的同题开发回归为 **4/6**，新报告 `docs/ADVENTUREWORKS_SQLITE_PORT_SECOND_RUN_20261001.json`，冻结题目和官方资产哈希均未改变。aw-02、aw-03 的物理投影和 JOIN 已正确，但各应有 19,119 个客户分组，当前只返回 100 行；系统输出达到上限告警，完整结果检查继续判失败。两轮成绩都由规则引擎产生，模型 API 调用数为零。

## 可复现命令

```powershell
python tools/fetch_adventureworks.py --directory D:/ICT8-OfficialDatasets/adventureworks
python tools/evaluate_adventureworks.py --directory D:/ICT8-OfficialDatasets/adventureworks --output docs/ADVENTUREWORKS_NEW_RUN.json
```

已有冻结资产和报告不会被覆写。新环境先安装项目依赖，再运行 fetch；已有数据无需再次 fetch。

赛题推荐 `chriseaton/adventureworks:postgres` 是第三方 PostgreSQL16 移植，其维护者说明遗漏部分 XML/CROSS APPLY 视图、函数、自定义类型和存储过程。Docker 在本机可运行，但现有引擎使用 sqlite3、sqlite_master、PRAGMA 和 SQLite authorizer，**尚不能直接接入该 PostgreSQL 数据库**。不为完成本审计而声称已补齐 PostgreSQL 支持。
