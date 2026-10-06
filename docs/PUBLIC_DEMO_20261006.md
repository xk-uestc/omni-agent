# 公网 demo 部署

地址：https://raysource.cloud/demo/ （现有域名含 r）。
Cloudflare 公共 DNS 查询：raysouce.cloud 返回 NXDOMAIN（Status=3），raysource.cloud 正常返回 A 记录。

链路：现有 Cloudflare Tunnel → 127.0.0.1:3011 的独立子路径网关 → 127.0.0.1:8030。
复用已有域名隧道基础设施，不修改 D:\lanqun-site 的文件。

## 启动与恢复

在本项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/start_public_demo.ps1
```

该脚本只启动未监听的 8030、3011 服务；端口被其他服务占用时退出，不终止其他进程。
隧道仍由现有 Cloudflare 隧道管理方式维护。本机保持开机联网、隧道和两项服务存活时公网可用。
本次没有配置新的开机启动任务。

3011 原服务在部署前未监听。当前网关只接收 /demo 和 /demo/ 下的路径；
域名根路径未恢复原站点，其他路径返回 404。若日后恢复原 3011 应用，需先在共享隧道中为
/demo 单独配置新端口，然后移动本项目网关，避免端口冲突。

## 子路径适配

- /demo 重定向 /demo/；页面相对资源保持可用。
- HTML 中以 / 开头的导航链接增加 /demo 前缀。
- HTML 注入轻量传输适配，将同域 /api/ 与 /health fetch 请求映射到 /demo 下。
  原页文件地址与完整性检查仍使用后端的原始路径，传输时才改写。
- 请求方法、查询参数、请求体与认证头转发；API 响应体不改写。
- SSE 异步逐块转发，无整段缓存；读取超时 330 秒。
- 上游断连返回 502；本机 8030 URL 保持可用。

## 实际访问记录

2026-10-06 使用 HTTPS 公网访问，证书验证开启，经本机网络代理出口：
/demo、/demo/、app.js、health、api/v1/nl2sql/schema、knowledge.html、capabilities.html 均返回 200。
公网 POST 流式问答接口返回 200 和 text/event-stream，首个事件约 0.77 秒，
24.08 秒收到 done 事件并结束。本记录是实际部署访问检查，未运行额外测试套件。

运行日志位于 runtime/public-demo.stdout.log 与 runtime/public-demo.stderr.log，不进入 Git。
网关不读取或输出模型密钥，模型仍由 8030 服务端读取本地配置。
