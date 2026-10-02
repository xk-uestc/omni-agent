# ICT 赛题八前端源码包

本包包含 Lattice Studio 前端页面、样式、交互脚本、数据库 Schema 字段定位 SVG 和相关回归测试。

## 页面

- `index.html`：问数主界面、SQL/执行审计和完整数据库字段图
- `knowledge.html`：知识库页面
- `capabilities.html`：赛题能力与验收证据页面

## 运行

此目录是前端源码，不含后端、数据库或模型凭据。页面通过 `/health`、`/api/v1/nl2sql/schema`、`/api/v1/agent/query` 等接口访问现有后端。推荐由项目后端或同源静态服务器托管此目录，并将 API 配置到同源地址；单独双击 HTML 可以查看静态布局，但实时问数、Schema 和知识库功能需要后端在线。

本包不包含数据集、运行数据库、密钥、模型配置或后端源码。

## Schema 图测试

在安装 Node.js 的环境中，从此目录执行：

```bash
node --test schema-svg.test.js
```
