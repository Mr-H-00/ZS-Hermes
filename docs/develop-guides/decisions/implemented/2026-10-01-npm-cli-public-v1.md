# 使用 npm 重构 Public v1 CLI

状态：implemented
类型：feature
Owner：packages/yuxi-cli/src/chat.ts

## 问题

现有 `yuxi-cli` 是独立 Python 包，包含非 Public v1 的旧 Agent 和知识库入口，并携带 eval/Langfuse 能力。需要一个面向普通用户、可通过 npm 安装、适配当前 Public v1 协议的基础 CLI；服务端不作为本地构建验证依赖。

## 决策

CLI 使用 npm 包 `@xerrors/yuxi`，要求 Node.js 22+，使用 TypeScript、ESM 和 JSON 本地配置。保留 remote、API Key/浏览器登录、身份状态、Agent 目录、Thread 对话与 SSE、知识库查询和 JSON 输出；`chat` 使用纯终端交互。HTTP 客户端拥有认证、错误、幂等键和 SSE 解析边界，命令层负责参数与呈现。

配置使用 `~/.yuxi/config.json`。eval、Langfuse、旧本地 HTML Chat、知识库上传和非 Public v1 管理接口不进入第一阶段。

### 实现方案

入口为 `yuxi` bin。Public Agent 使用 `/api/v1/agents`，会话使用 `/api/v1/agents/sessions`（见[Session 协议决定](2026-10-09-agents-session-protocol.md)） 及其事件、快照和 SSE 路由，知识库使用 `/api/v1/knowledge/tools` 的列库、文件搜索、检索、打开和查找接口（见[接口收敛决定](2026-10-09-remove-knowledge-external-api.md)）；系统 discovery、health 和 CLI auth 使用现有公开系统/认证路由。`ChatSession` 拥有 Thread cursor、Turn 过滤、逻辑事件去重和有限断线恢复，正常输出消费 `output_text.delta`，流中断、EOF 或 resync 时回读指定 Turn 的持久结果；该观察行为由[查询与恢复决定](2026-10-09-agents-query-recovery.md)拥有。网络失败、非 JSON 响应、SSE 解析失败和服务端错误转换为非零退出与可读错误。

## 替代方案

- 继续维护 Python 包：不采用，无法满足 npm 分发和 Node 生态要求。
- 保留旧 TOML 配置：不采用，第一阶段以 JSON 作为配置 Owner。
- 保留本地浏览器 Chat：不采用，第一阶段聚焦终端和 Public v1 主链路。
- 继续保留 eval：不采用，用户明确排除该能力。

## 后果

npm 包可以独立构建和发布，不需要 Python 或 Docker。已有 Python CLI 的 TOML 配置不会自动迁移，用户需要在 npm CLI 中重新登录。Thread 事件中的未知扩展字段会被安全忽略，最终结果仍依赖 Public v1 的持久状态和终态事件。

## 验证

| 验收主张 | 直接证据 / 命令 | 结果 |
|---|---|---|
| 包可按 npm 规范构建并暴露 `yuxi` 命令 | `cd packages/yuxi-cli && npm run typecheck && npm test && npm run pack:check` | Passed |
| 配置、认证错误、Public v1 KB 路径、SSE 和 Chat 生命周期具备静态回归覆盖 | `npm test`，8 tests passed；包含本地 HTTP server 路由核对 | Passed |
| 工程契约有效 | `python3 scripts/verify_engineering_contracts.py` | Passed |
| 远程健康接口和多轮多行 Chat 可被新 CLI 访问 | 临时 `HOME` 下执行 `remote ping` 和 `status`；真实 Public v1 两轮 Chat 回读 delta、cursor 和多行结果 | Passed |
| eval 和 Python package 不再作为 npm 包表面存在 | `rg` 检查；npm dry-run 仅包含 dist、README 和 package metadata | Passed |

旧能力不存在：`agent eval`、Langfuse、Python package、旧 HTML Chat、上传命令均未进入新包。

重新引入条件：只有 Public v1 为对应能力提供稳定公开契约，并新增独立需求与验证后，才重新评估这些能力。
