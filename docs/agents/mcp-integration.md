# 集成 MCP

MCP（Model Context Protocol）让智能体调用外部服务提供的工具。管理员在“扩展 → MCP”中添加远程服务器，智能体配置再决定哪些服务器进入运行时。

## 支持的传输方式

| 传输方式 | 适用场景 |
| --- | --- |
| `streamable_http` | 新的远程 MCP 服务 |
| `sse` | 仍提供 SSE 接口的远程服务 |
| `stdio` | 仅限代码维护的系统内置 MCP |

管理接口只接受 `streamable_http` 和 `sse`。Yuxi 不允许通过 HTTP 请求创建任意本地 `stdio` 进程；历史用户 `stdio` 配置会被禁用，应迁移为远程服务。

## 添加远程 MCP

在“扩展 → MCP”点击“添加 MCP”，填写稳定标识、名称、传输方式和 URL。例如：

```json
{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp"
}
```

管理接口对应：

```http
POST /api/system/mcp-servers
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "description": "提供示例查询工具"
}
```

需要认证的远程服务可以配置 HTTP headers、连接超时和 SSE 读取超时。凭证会随着连接请求发送，请只配置必要的 header，并把管理接口限制在可信的管理员范围。

添加后先点击“测试连接”，确认能发现工具，再把服务器状态设为“已添加”。状态关闭时，服务器记录仍保留，但不会进入运行时。

## 让智能体使用 MCP

在智能体配置的 MCP 字段中选择已添加的服务器：

- 未显式配置时，使用当前用户可见的全部已启用服务器；
- 显式选择后，只使用选择项；
- MCP 工具仍会在执行处使用当前用户身份和服务器配置；
- 管理员可以在 MCP 详情页单独禁用某个工具。

MCP 配置从 PostgreSQL 读取，工具对象按配置哈希缓存。修改连接配置或工具禁用列表后，下一次运行会使用新的配置键。

## 添加内置 stdio MCP

`stdio` MCP 等价于在 API/worker 容器内启动一个进程，只适合经过代码审查且必须本地运行的系统能力。远程服务可以承载时，优先使用 SSE 或 Streamable HTTP。

开发者在 [`service.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/mcp/service.py) 的 `_DEFAULT_MCP_SERVERS` 中添加固定定义：

```python
_DEFAULT_MCP_SERVERS = {
    "example-mcp": {
        "command": "npx",
        "args": ["-y", "@scope/example-mcp@1.2.3"],
        "transport": "stdio",
        "description": "说明具体能力和使用场景",
        "icon": "🧩",
        "tags": ["内置"],
    },
}
```

`command`、`args` 和 `env` 必须是代码中的固定值，包版本必须锁定；不能从 HTTP 请求、数据库字段或不受信任的环境拼接。`env` 只放非敏感固定值，密钥不能提交到代码或同步到数据库。

API/worker 启动时会把内置定义同步到数据库。新内置 MCP 默认未添加，管理员需要在管理页启用；内置项的连接配置由代码维护，不能删除或通过页面改成其他进程。

验证新内置 MCP：

```bash
docker compose up -d --build api worker
docker compose logs --tail=100 api worker
```

然后在管理页添加并测试工具。测试只确认连接和工具发现，不要在没有隔离和授权的情况下执行文件写入、Shell 或其他副作用。

::: danger 安全边界
MCP 工具的副作用等同于外部服务或本地进程本身的副作用。审查依赖来源、固定版本、命令参数、网络访问和数据权限；不要把 `SANDBOX_PROVISIONER_TOKEN`、数据库密码或对象存储管理凭据注入 MCP 或 Agent 沙盒。
:::

## 常用管理接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/system/mcp-servers` | 查看服务器；普通用户只得到脱敏基础信息 |
| `POST` / `PUT` | `/api/system/mcp-servers`、`/{slug}` | 添加或修改远程 MCP |
| `PUT` | `/api/system/mcp-servers/{slug}/status` | 添加或移除服务器 |
| `POST` | `/api/system/mcp-servers/{slug}/test` | 测试连接并发现工具 |
| `GET` | `/api/system/mcp-servers/{slug}/tools` | 查看工具 |
| `PUT` | `/api/system/mcp-servers/{slug}/tools/{tool_name}/toggle` | 启用或禁用单个工具 |

接口字段和错误响应以实例 Swagger 为准。
