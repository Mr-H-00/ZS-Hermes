# WPS 云盘

企业智能体网页的网盘提供「工作区」和「我的成果」。WPS 云盘、云盘配置及网页授权回调已下线。本页记录仍由 identity 模块维护的 WPS 接口。

## 配置与授权

WPS 配置接口接收应用 ID、应用密钥和公网 HTTPS 回调。回调路径固定为 `/auth/wps/callback`，不接受 IP、localhost、查询参数、片段或内嵌凭据。WPS 控制台登记的回调必须与服务端配置一致。正式接入使用部署方拥有的域名与 HTTPS 入口。

应用密钥与个人 access/refresh token 以领域隔离密钥加密保存。持久 `API_KEY_DERIVATION_SECRET` 变化会影响既有凭据解密；不要把它放入前端或 Agent 沙盒。授权交换只能消费当前用户的有效一次性 state。应用配置发生变化后，旧连接需重新授权。

WPS 文件列表、搜索和文档链接来自当前用户连接。连接或刷新失败时，接口返回相应的错误。

## 开发隧道

开发 Compose 仅向 API 只读挂载 `CLOUDFLARED_CONFIG_DIR`，默认是 `docker/cloudflared`。`config.example.yml` 给出新 `frontend` 服务的转发目标。实际 config、Tunnel credentials JSON 和域名 DNS 权限由部署方提供，凭据不能提交。

当前开发联调流程使用既定命名隧道与固定回调域名。配置不符合该流程时显式拒绝，不自动创建 DNS 或任意公网入口。API 按用户请求启动隧道，通过前端页面与当前 API 随机回声确认入口，进程退出时回收自己启动的隧道。生产 Compose 不发布开发隧道。

## 验收边界

协议回放可验证 state 重放拒绝、配置权限、用户隔离、加密保存和 token 刷新；隧道测试可验证进程生命周期与探针。真实接入仍需 WPS 应用权限、控制台回调、DNS、Cloudflare 凭据及浏览器授权结果。企业智能体网页端的 WPS 流程不参与当前部署验收。
