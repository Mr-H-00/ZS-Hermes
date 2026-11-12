# WPS 云盘配置与本人连接

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/cloud_drive_service.py

## 问题

用户需要在设置中保存 WPS 365 自建应用配置，并用本人授权访问云文档；现有我的云盘展示静态示例，不能证明真实连接或提供文件操作。用户明确暂不开发 AnyShare，并要求移除其云盘入口与配置。

## 决策

设置入口位于基本设置之后、OCR 配置之前，只包含 WPS 365 云文档。管理员维护共享应用配置，普通登录用户可完成本人连接。独立 WPS 应用配置和按用户隔离的连接表由 PostgreSQL 拥有；路由只装配 HTTP 请求，服务协调 OAuth 与云文档协议，repository 拥有持久化查询。密钥读取仅返回是否配置，留空保存保留原值，显式清除删除原值；密钥与令牌使用现有持久安全密钥派生的领域密钥加密保存。授权回调使用已登记的公网 HTTPS 域名，拒绝 localhost、IP 与多余参数；浏览器从同一域名登录后发起授权，一次性 state 绑定当前用户，实际获取用户身份成功后才标记已连接。开发环境的独立临时回调见[开发联调决定](./2026-10-14-wps-development-tunnel.md)。我的云盘从后端读取当前用户的连接和文件数据，提供文档库、目录、搜索、打开与下载。AnyShare 的云盘占位与示例标记一并移除。

## 替代方案

- 只用浏览器存储配置与连接标记：无法跨浏览器使用，也不能证明授权或执行后端访问隔离。
- 同时新增 AnyShare：缺少可确认的部署与 API 契约，且用户已明确取消。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 云盘配置位于指定位置且只含 WPS | 桌面与移动导航不一致、仍出现 AnyShare | `SettingsModal.vue`、云盘组件 | 前端 9 项云盘测试、lint、build | 顺序错乱或 AnyShare 仍可见 | Passed |
| 保存配置后真实回读且密钥不回传 | 空输入意外删除密钥、密钥泄漏 | 配置 service/repository | WPS HTTP integration 与 PostgreSQL 回读 | 非管理员写配置、清除与保留错误 | Passed |
| 本人授权成功才展示连接 | 保存配置即被标记连接、state 被其他用户消费 | OAuth service/repository | WPS HTTP integration 与协议 unit | 错误 state、重放回调、相邻用户凭据 | Passed |
| WPS 云盘使用后端返回的授权结果 | 示例文档伪装真实文件、未连接仍访问 | 云盘 service、前端 API 与组件 | WPS HTTP integration、前端测试 | 未授权或过期连接读取文件 | Passed |
| 设置与云盘页面的真实 DOM 和截图符合预期 | 组件测试不能识别真实页面的视觉与交互问题 | 设置与云盘组件 | 浏览器页面检查 | loading、empty、error 和固定回调展示 | Not run：浏览器工具初始化失败 |
| 真实 WPS provider 联通 | 外部协议、应用权限和回调登记不匹配 | WPS provider | 真实应用凭据与外部回调探针 | provider 拒绝授权或文件接口 | Not run：未提供应用凭据 |

## 后果

真实 WPS 授权与文件调用依赖已发布自建应用、API 权限和注册回调地址。当前确定性协议与真实 PostgreSQL/HTTP 隔离测试不能代替外部 provider 联通；共享应用配置修改后，旧用户连接必须重新授权，避免继续使用另一应用的凭据。AnyShare 没有运行时入口、配置字段或迁移表。
