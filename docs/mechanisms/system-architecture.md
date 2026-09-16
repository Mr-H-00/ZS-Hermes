# Yuxi 系统架构

本页面向需要理解部署拓扑、AgentRun 执行和知识库数据流的开发者与运维人员。图中的当前事实来自 [`ARCHITECTURE.md`](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)、[`docker-compose.yml`](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml) 和各机制 Owner；状态、权限、文件隔离与失败恢复的完整语义仍以专题页和源码为准。

## 架构总览

Yuxi 由 Web、API、worker、一次性迁移器和基础设施服务组成。API 负责 HTTP 适配与请求接入，worker 负责长生命周期执行；PostgreSQL 保存业务最终事实，Redis 负责投递、短期事件、取消和缓存。

![Yuxi 系统架构总览](/architecture/system-overview.svg)

### 组件职责

| 层 | 组件 | 拥有或执行的事实 |
| --- | --- | --- |
| 用户入口 | `web`、HTTP 客户端 | 发起配置、聊天、文件和知识库操作；消费 API 响应与 SSE |
| 接入层 | `api`、`server/routers` | 认证、输入校验、路由注册和响应装配 |
| 用例层 | `yuxi.services` | 请求接入、事务边界、Run 生命周期、任务编排和资源装配 |
| 执行层 | `worker`、Agent Runtime | 获取 lease，执行 LangGraph、工具、子 Agent、定时 Agent 和 Durable Task |
| 业务事实 | PostgreSQL | Message、AgentRunRequest、AgentRun、权限、知识库元数据、Task 和 LangGraph checkpoint |
| 投递平面 | Redis / ARQ | 任务投递、运行事件 Stream、取消信号和短期缓存；不拥有业务终态 |
| 文件对象 | UserWorkspace、MinIO | UserWorkspace 保存 Project Workdir 和产物字节；MinIO 保存知识库原文件、解析结果和对象 |
| 检索图谱 | Milvus、etcd、Neo4j | Milvus 保存向量和检索字段；etcd 协调 Milvus；Neo4j 保存可选图谱投影 |
| 解析能力 | MinerU、PaddleX | `all` profile 下提供文档解析、版面分析或 OCR；不是默认应用入口 |
| 沙盒 | `sandbox-provisioner`、动态 Runtime | 创建、发现、代理和回收隔离运行时；运行时按当前 uid、scope、Workdir 和 generation 校验 |

## AgentRun 请求与执行

普通聊天把 Request 接入和 Run 执行分成两个阶段。数据库提交是队列投递的前置条件；同一用户、Agent 和线程的普通请求按 FIFO 派发。

![AgentRun 请求与执行](/architecture/agent-run-flow.svg)

关键时序和 Owner：

- API 在 PostgreSQL 提交 Message 与 `AgentRunRequest` 后才向 Redis/ARQ 投递；投递失败时持久化请求仍可由 publisher 或补偿逻辑观察。
- worker 只有取得当前 attempt lease 才能执行和写入 Run；heartbeat 由当前 owner 续租，失联后由 reconciliation 收敛为带原因的终态。
- Redis Stream 只服务实时观察。前端断线恢复、最终结果、阶段时间和审计数据必须回读 PostgreSQL。
- Run 的展示输出由同一 `run_id` 的 assistant Message 通过 `output_message_id` 绑定；不能从相邻 Run 猜测结果。
- resume 会从对应 LangGraph checkpoint 重建上下文并创建新的 Run，不重新走普通消息 FIFO 接入。

详细状态、线程阅读数据、审计和恢复规则见[Agent 运行时上下文](./agent-runtime.md)；请求排队策略见[Agent 请求队列](../agents/agent-request-queue.md)。

## 知识库导入与检索

知识库管理动作由 Durable Task 驱动，文档状态和外部存储投影分开观察。Milvus 知识库支持上传、解析、分块、索引和图谱；Dify、Notion 只作为只读连接器。

![知识库导入、检索与图谱任务](/architecture/knowledge-flow.svg)

解析、索引和图谱构建的关键边界：

- PostgreSQL 保存知识库配置、权限、文件状态、chunk、Task 和 lease；MinIO 保存原文件与解析产物；Milvus 和 Neo4j 保存可重建的检索或图谱投影。
- 文件处理按 `(kb_id, file_id)` 串行，状态认领、processing owner 和外部副作用必须保持同一文件的 owner 语义。
- 解析或索引失败会把文件收敛到对应错误状态；任务状态为完成不能单独证明 Markdown、chunk 或向量已经正确写入。图谱由独立的 `knowledge_graph_index` Durable Task 锁定配置、记录状态并写入 Neo4j 与图向量投影。
- Agent 先计算用户可见知识库与 Agent 选择的交集，再由工具执行处重新校验 `kb_id`、`file_id` 和文件名。知识库不会挂载到 Sandbox 目录。
- 解析服务通过 `all` profile 可选启动。完整模式才装配知识路由、知识 Durable Task、评估和图谱；LITE 模式保留聊天、AgentRun、Workspace、MCP、非知识 Skills 和模型能力，但跳过知识域重运行时。

完整状态机、外部存储回读和失败恢复见[知识库机制](./knowledge-base.md)；解析器配置见[文档处理与 OCR](../advanced/document-processing.md)。

## 文件、Workdir 与沙盒

同一份持久文件由不同入口以不同能力访问：

![Workdir 与 Sandbox 边界](/architecture/workdir-sandbox.svg)

`runtime_scope_id` 把同一顶层执行树的根 Agent 与子 Agent 绑定到同一个运行时；子 Agent 使用独立 checkpoint thread，但继承根 Conversation 的 Workdir。Run 终态清理运行时进程并保留 Workdir 文件。宿主机路径、Sandbox 虚拟路径和对象存储 URL 在各自边界转换，不能互相替代。

详见[沙盒与文件系统](./sandbox.md)。

## 模式与启动边界

| 模式 | 装配能力 | 跳过能力 |
| --- | --- | --- |
| 完整模式 | 认证、Web/API、Agent、AgentRun、FIFO、MCP、Skills、Workspace、知识库、评估、图谱、Durable Task、Sandbox | 无 |
| `LITE_MODE` | 认证、Web/API、Agent、AgentRun、FIFO、MCP、非知识 Skills、Workspace、模型和系统管理 | 知识 Schema、知识路由、知识 Durable Task、评估、图谱、`knowledge-base` Skill 和知识工具 |

`storage-migrator` 是 Compose 中唯一修改 Yuxi PostgreSQL Schema 的服务。API 与 worker 在启动时只校验所需 Schema 版本；`/api/system/health` 只表达进程存活，`/api/system/ready` 才表达接收真实流量所需的基础条件。

## 事实 Owner 与源码定位

| 主题 | 当前事实 Owner | 入口 |
| --- | --- | --- |
| 服务拓扑、环境传播和 profiles | Docker Compose | [`docker-compose.yml`](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml) |
| 系统边界和主链路 | 架构代码地图 | [`ARCHITECTURE.md`](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md) |
| HTTP 路由与能力发现 | FastAPI 路由注册 | [`backend/server/routers/__init__.py`](https://github.com/xerrors/Yuxi/blob/main/backend/server/routers/__init__.py) |
| Request/FIFO/Run 接入 | 用例服务与 repositories | [`agent_request_queue_service.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/agent_request_queue_service.py)、[`agent_run_service.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/agent_run_service.py) |
| Agent 图与 checkpoint | Agent runtime | [`base.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/base.py)、[`chatbot/graph.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/chatbot/graph.py) |
| Durable Task worker | Task service、registry 与 ARQ worker | [`task_service.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/task_service.py)、[`run_worker.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/run_worker.py) |
| 知识库状态、executor 与检索 | `yuxi.knowledge` | [`knowledge-base.md`](./knowledge-base.md) |
| Workdir 与文件隔离 | `yuxi.workspace`、Sandbox backend | [`sandbox.md`](./sandbox.md) |
| Schema 迁移与模式边界 | storage migration、runtime config、lifespan | [`storage_migration.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/storage_migration.py)、[`runtime.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/config/runtime.py) |

文档页面构建只能证明 SVG、Markdown、导航和链接可处理，不能替代真实 PostgreSQL、worker、SSE、文件、对象、Milvus、Neo4j 或 Sandbox 验证。改动上述边界时，按[测试规范](../develop-guides/testing-guidelines.md)选择对应的 integration 或 E2E 证据。
