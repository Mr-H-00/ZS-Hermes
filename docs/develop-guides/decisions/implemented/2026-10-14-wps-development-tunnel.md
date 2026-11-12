# WPS 开发联调命名 Tunnel 回调

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/wps_development_tunnel.py

## 问题

WPS 正式环境不接受 localhost 或 Cloudflare Quick Tunnel 回调。开发者需要从设置中取得固定的公网 HTTPS 地址，同时保留正式回调字段，避免联调地址覆盖正式配置。

## 决策

设置中的用户授权回调地址下方提供“开发联调”区。管理员在 Docker Compose 开发环境中显式启动或停止已经创建的 Cloudflare 命名 Tunnel `zs-dev`。Tunnel 名称和公网主机名在服务中固定为 `zs-dev` 与 `zs-dev.zs-wps.com`，不能被环境变量或请求覆盖；config 和 credentials 文件由部署者通过只读挂载提供，API 只执行 `cloudflared tunnel ... run zs-dev`，不会在请求中创建 Tunnel、绑定 DNS 或接收凭据。启动时使用固定 HTTPS 地址 `https://zs-dev.zs-wps.com` 并通过本站页面和随机探针验证公网请求确实到达当前实例。页面单独展示、复制和打开联调回调，正式回调字段不被修改。

联调授权使用当前固定域名，PostgreSQL 在一次性 OAuth state 上记录实际回调和隧道生命周期标识，换取令牌使用同一回调。停止、进程退出或 API 关闭会回收子进程并撤销当前地址；旧联调 state 不能在隧道停止或重启后继续完成。生产环境和非 Compose 运行环境拒绝启动联调。

## 替代方案

- 只显示手动隧道命令：设置页面不能确认实际地址已经可达。
- 把临时地址写入正式回调字段：隧道变化会破坏已登记的正式配置。
- 接受前端传入任意命令或代理目标：会把 API 变成不受控的网络代理。

## 后果

命名 Tunnel 的创建和 DNS 绑定是部署侧一次性前置操作：`cloudflared tunnel login`、`cloudflared tunnel create zs-dev`、`cloudflared tunnel route dns zs-dev zs-dev.zs-wps.com`，之后将生成的 UUID credentials JSON 与 config 放入 `CLOUDFLARED_CONFIG_DIR`。启动或 API 重启不会改变域名，WPS 后台登记值固定为 `https://zs-dev.zs-wps.com/auth/wps/callback`。Cloudflare 外部网络、DNS 解析、WPS 应用权限和实际授权仍属于部署侧前置条件；没有真实 WPS 凭据时不宣称外部 provider 已联通。API 镜像包含固定版本的 Cloudflare 二进制及 amd64/arm64 校验值。

## 验证

| 验收主张 | 直接证据 | 负向案例 | 当前结果 |
|---|---|---|---|
| 联调区独立展示且正式回调保持不变 | `web/test/unit/cloudDrive.test.js`、Web lint/build | 启停后正式字段不变 | Passed |
| 只在开发 Compose 环境启动已配置的命名子进程 | `backend/test/unit/services/test_wps_development_tunnel.py` | 生产/非 Docker、分别缺少 config/credentials、缺少组件、环境变量不能覆盖固定名称与域名 | Passed |
| 停止联调撤销回调并回收子进程 | 隧道生命周期 unit test、API lifespan cleanup | 停止后旧地址和 OAuth state 失效 | Passed |
| 固定域名页面和随机探针到达当前实例后才发布地址 | 公网探针实现检查；真实 Cloudflare 域名验证 | 错误 DNS、页面或探针归属不匹配 | Inspected；真实域名未验证 |
| OAuth 使用同一固定回调且停止后旧 state 失效 | `backend/test/integration/api/test_cloud_drive_router.py`、PostgreSQL schema test | 跨生命周期 state、正式回调被覆盖 | Passed |

已执行：命名 Tunnel 单元与 WPS 协议测试（26 passed）、WPS HTTP/PostgreSQL 集成测试（10 passed）、前端云盘单元测试（9 passed）、Web lint/build、`docker compose config --quiet`，以及当前 API 容器内 `cloudflared 2026.10.0` 的版本和 `tunnel run` 参数检查。工程契约脚本因主机没有可用的 Python 解释器未执行。API 全量非慢速单测在 1669 passed、57 skipped 后长时间停止进展，已中断，不能视为全量通过；浏览器工具初始化失败，未完成真实页面截图验证。未执行真实命名 Tunnel DNS/公网探针和 WPS provider 授权；部署者须拥有目标域名并在 Cloudflare 管理其 DNS，完成账号授权、Tunnel 创建、DNS 绑定并放入 credentials/config。
