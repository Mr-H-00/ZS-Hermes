# 接入 OIDC 登录

Yuxi 可以通过 OpenID Connect（OIDC）接入企业身份提供商。功能默认关闭；开启前，需要在身份提供商中注册客户端，并准备一个与 Yuxi 地址完全一致的回调地址。

## 1. 在身份提供商中注册客户端

创建一个 OIDC 客户端，记录：

- Client ID；
- Client Secret；
- Issuer URL。

把下面的后端回调地址注册到允许的 Redirect URI。生产环境请替换为实际的 Yuxi 域名并使用 HTTPS：

```text
https://<your-yuxi-host>/api/auth/oidc/callback
```

本机开发可以使用 Vite 代理后的 `http://localhost:5173/api/auth/oidc/callback`，前提是身份提供商允许该回调地址；不要在不受信任的网络中使用 HTTP。Yuxi 部署在反向代理后时，回调地址必须是用户实际访问的外部地址，而不是容器内部地址。

## 2. 配置 Yuxi

在 `.env` 或生产环境使用的 env file 中设置：

```bash
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=企业登录
OIDC_ISSUER_URL=https://auth.example.com
OIDC_CLIENT_ID=<your-client-id>
OIDC_CLIENT_SECRET=<your-client-secret>
OIDC_REDIRECT_URI=https://<your-yuxi-host>/api/auth/oidc/callback
```

没有配置 `OIDC_AUTHORIZATION_ENDPOINT` 时，Yuxi 会根据 `OIDC_ISSUER_URL` 请求 `/.well-known/openid-configuration`，读取授权、换 token 和 UserInfo 端点。只要填写了 `OIDC_AUTHORIZATION_ENDPOINT`，代码就把手动端点当作权威配置并跳过 discovery；这时 `OIDC_TOKEN_ENDPOINT` 和 `OIDC_USERINFO_ENDPOINT` 也要按实际流程补齐，否则回调换 token 或读取用户信息会失败。

| 变量 | 默认值或说明 |
| --- | --- |
| `OIDC_AUTHORIZATION_ENDPOINT` | 授权端点；可从 discovery 获取 |
| `OIDC_TOKEN_ENDPOINT` | Token 端点；可从 discovery 获取 |
| `OIDC_USERINFO_ENDPOINT` | UserInfo 端点；可从 discovery 获取 |
| `OIDC_END_SESSION_ENDPOINT` | 登出端点；可从 discovery 获取 |

用户信息和账号策略：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `OIDC_SCOPES` | `openid profile email` | 请求的 scope |
| `OIDC_AUTO_CREATE_USER` | `true` | 找不到本地账号时是否创建用户 |
| `OIDC_DEFAULT_ROLE` | `user` | 自动创建用户的角色 |
| `OIDC_DEFAULT_DEPARTMENT` | `OIDC用户` | 自动创建用户没有部门信息时使用的部门 |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | 用户名字段 |
| `OIDC_EMAIL_CLAIM` | `email` | 邮箱字段 |
| `OIDC_NAME_CLAIM` | `name` | 展示名称字段 |
| `OIDC_FORCE_PROMPT_LOGIN` | `true` | 是否在授权请求中加入 `prompt=login` |

可选的账号绑定和部门映射：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `OIDC_USE_RAW_USERNAME` | `false` | 是否用 OIDC 用户名作为 Yuxi 的 `uid` |
| `OIDC_FETCH_DEPARTMENT_INFO` | `false` | 是否读取 UserInfo 中的部门并创建/关联部门 |
| `OIDC_DEPARTMENT_CLAIM` | `department` | 部门名称字段 |

`OIDC_CLIENT_SECRET` 和其他凭证只能放在受保护的运行环境中，不要提交到仓库或打印到日志。生产环境的 Issuer、回调地址和端点使用可信的 HTTPS 地址；本机开发只使用 localhost HTTP。

## 3. 重启 API

OIDC 配置在 API 进程启动时读取。修改环境变量后需要重新创建 API 容器，而不是只重启原容器。开发 Compose：

```bash
docker compose up -d --force-recreate api
```

生产 Compose：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate api
```

打开登录页后，OIDC 按钮会使用 `OIDC_PROVIDER_NAME` 显示的名称。登录流程是：

1. 前端向 `/api/auth/oidc/login-url` 请求授权地址。
2. 身份提供商登录后回调 `/api/auth/oidc/callback`。
3. API 校验一次性 `state`，用授权码换取 token 并读取 UserInfo。
4. API 将一次性登录 code 交给前端 `/auth/oidc/callback` 页面。
5. 前端使用 code 调用 `/api/auth/oidc/exchange-code`，再进入 Yuxi。

## 使用原始用户名绑定已有账号

将 `OIDC_USE_RAW_USERNAME=true` 后，Yuxi 会尝试用 OIDC 返回的用户名匹配已有的 `uid`。匹配成功后，系统会创建一条已删除状态的占位用户记录，保存 `OIDC sub` 与目标用户的绑定关系；占位记录不能用于登录。

绑定占位用户的 `uid` 格式是 `oidc:{sub}:{target_user_id}`：其中 `target_user_id` 是数据库 `users.id` 的数值，用户实际登录标识仍是字符串 `uid`。`sub` 中即使包含冒号，系统也会从最后一个冒号解析目标 ID。

如果同一个 OIDC `sub` 已绑定到其他用户，登录会被拒绝，防止因为用户名相同而接管账号。启用此模式前，请确认 OIDC 用户名在身份提供商中稳定且唯一，并提前创建需要绑定的 Yuxi 用户。

## 从 UserInfo 获取部门

开启 `OIDC_FETCH_DEPARTMENT_INFO=true` 后，系统从 `OIDC_DEPARTMENT_CLAIM` 读取部门名称：

- 部门名称会去除首尾空格并截断为 50 个字符；
- 部门描述会截断为 255 个字符；
- 部门名称为空时使用 `OIDC_DEFAULT_DEPARTMENT`；
- 不存在的部门会自动创建，并把新用户关联到该部门。

如果组织的部门字段不是字符串或需要复杂的层级映射，请先在测试环境验证，必要时在身份提供商侧提供稳定的扁平字段。

## 排查登录失败

1. 检查 API 是否读取了 `OIDC_ENABLED=true` 和完整的 Client 配置。
2. 检查身份提供商登记的 Redirect URI 是否与 `OIDC_REDIRECT_URI` 完全一致。
3. 检查 API 能否访问 Issuer 的 discovery 地址和 UserInfo 端点。
4. 检查用户信息中是否存在配置的 username、`sub` 和必要的 email 字段。
5. 查看 API 日志中的 OIDC 错误；不要把 Client Secret 或 token 一并贴到日志中。

登录页没有 OIDC 按钮通常表示 OIDC 未启用或基础配置不完整；回调失败时，页面会回到登录页并显示可读错误。
