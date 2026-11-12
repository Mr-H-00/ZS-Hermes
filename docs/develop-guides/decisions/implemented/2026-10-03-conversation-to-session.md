# Conversation 统一为 Session 并保留 Thread 执行身份

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/models/sessions.py

## 问题

面向贡献者与维护者，本决定明确业务会话、执行线程与消息展示组的命名边界。完成标准是前后端、数据库和当前契约使用一致的 Session 术语，同时维持 Thread、Turn、Run、权限、队列与最终输出归属。多 Thread 会话层级、调度策略调整和运行框架替换不属于命名统一的默认范围。

`Conversation` 保存整数主键、唯一 `thread_id`、用户与 APP 归属、Agent、Project、会话默认配置及生命周期。Input、Receipt、Turn 和 Run 使用 `conversation_thread_id` 标识执行线程；Message 与 Run 使用整数 `conversation_id` 关联业务行。公开事件已经使用字符串 `session_id = thread_id`，普通公开入口使用 `/threads`。前端部分 `conversations` 保存按 Run 分组的消息，而侧栏使用同名术语表达长期会话。

Schema 初始化入口只接受空库或精确当前版本；已有版本不会自动升级。重命名数据库表和列需要明确新库部署或旧数据迁移的取舍。

## 决策

### 实现方案

保持业务 Session 与 Thread 一对一。Session 拥有长期交互的业务记录；Thread 保持运行身份、checkpoint、FIFO、安全边界、SSE 订阅和子线程语义。创建 Thread 的现有用例继续创建一条业务 Session，输入事务提交后再发布 ARQ，Turn 的最终输出继续通过 `result_run_id` 读取。

| 当前含义 | 当前名称 | 事实 Owner |
| --- | --- | --- |
| 业务会话实体、仓储与表 | `Session`、`SessionRepository`、`sessions` | agents models/repositories |
| 整数业务行关联 | `session_record_id`，父子关联使用相同后缀 | Message、AgentRun、SubagentThread、workspace bindings |
| 字符串执行线程关联 | `thread_id` | Input、Receipt、Turn、Run 与复合约束 |
| 已有事件会话身份 | 保留字符串 `session_id = thread_id` | events、公开事件 adapter 与前端 stream handler |
| 会话界面与侧栏 | session 模块及 Session 组件命名 | frontend modules、pages 与导航 |
| 单次运行或连续执行展示组 | 按实际粒度使用 `runGroups`、`messageGroups` | messageProcessor、连续执行分组与 workspace |

数据库连接继续使用 `db`、`db_session` 和 `AsyncSession`；业务实体变量使用 `agent_session` 等明确名称，避免覆盖数据库事务变量。数据库主键类型、Thread ID 值与 checkpoint key 保持现有定义。

管理员接口统一为 `/dashboard/sessions`，统计与定时任务返回值同步使用 Session 字段。公开 Run 快照使用 `thread_id`；旧字段、旧路由不保留。测试直接请求旧管理员路由，必须返回 404。

业务 Schema 版本升为 2，知识库版本保持 1。启动入口拒绝旧版本与包含未版本化旧 `conversations` 表的数据库；新环境创建 `sessions`、复合外键与索引。本任务不提供数据迁移。

摘要文件目录沿用 DeepAgents 默认的 `outputs/conversation_history`，媒体目录与文件工具结果路径继续由依赖初始化。本次仅统一业务实体和字段，不重命名第三方文件目录，不覆盖 `_history_path_prefix`、`_media_prefix` 或 `_conversation_history_prefix`。Yuxi 的路径常量与测试保留对应目录名；文件访问继续由 Workdir 授权和路径校验执行，不按摘要目录名新增拒绝。

冻结的 archived 决策、历史发布说明及历史验证命令保留当时的词汇；当前源码、契约、机制说明与仍存续的 Owner 路径使用新术语。

### 已确认边界

- 已确认数据库：使用独立新库，不实现旧库迁移；禁止修改运行中原槽位。
- 已确认关系：沿用一对一，只统一业务名称。
- 已确认身份：采用 `session_record_id` 区分整数主键，原 `conversation_thread_id` 改为 `thread_id`；前端展示组按实际语义命名。
- 已确认外部兼容：公开 Run 字段、管理员路由和相关集成一次切换，不保留旧字段或路由。

## 替代方案

机械替换所有 conversation 为 session：变更方式简单，但整数 `session_id` 与已有字符串事件 `session_id` 会发生语义碰撞，消息展示组也会错误地被称为多个业务 Session。

新增 Session → 多 Thread 层级：需要重新定义作用域、Agent 配置、Project/workdir、队列、子线程、权限及删除归属，改变验收结果，应作为独立架构需求。

只修改前端词汇并保留数据库名称：避免 Schema 切换，但无法满足数据库与后端统一的目标。

引入 Agents SDK Session 作为另一套历史存储：需要重定义与 PostgreSQL Message、LangGraph checkpoint、摘要和恢复的关系，增加重复事实源；当前命名目标没有这一 consumer。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| Session 业务身份与 Thread 执行身份保持区分 | 整数关联冒充事件身份，遗漏旧导入 | ORM、repository、序列化与前端模块 | 后端全量 unit 2524 passed、55 skipped；前端 unit 445 passed，lint/build 通过 | 公开 Run 旧字段缺失，旧路由 404 | Passed |
| Schema 和复合约束一致 | 旧库被误接受、新表漏约束 | migrations、PostgreSQL schema 与 ORM | 六个文件 PostgreSQL integration 共 47 项最终通过（首次 46 passed、旧版本 oracle 1 项失败，显式更新 oracle 后 schema 9 项重跑全过）；新库回读 business=2、knowledge=1、sessions 存在、conversations 不存在 | 旧版本、未版本化 sessions/conversations 表拒绝启动；跨 Thread/Run 关联拒绝 | Passed |
| 输入队列与最终输出归属保持 | SSE、FIFO、等待恢复或子 Thread 退化 | inputs、scheduler、events、subagents | 真实 HTTP integration 47 项最终通过（dashboard/chat 33 项重跑）；确定性 lifecycle E2E 32 passed，回读终态、输出和文件 | 越权及跨 Thread/Turn/Run 归属错误拒绝 | Passed |
| 当前 UI 与调用契约一致 | 旧字段消费者、消息分组求值异常 | frontend APIs、workspace、dashboard 与 schedules | Playwright Agent 编辑/创建、真实消息完成后刷新恢复、管理员列表 | 页面异常与旧接口调用作为失败条件 | Passed |
| 摘要路径和文件授权保持现有语义 | 摘要路径不一致或文件授权被改变 | sandbox paths、summary、filesystem 与 artifacts | 恢复依赖默认目录后，全量 unit 2524 passed、55 skipped；既有生命周期文件验证 | 路径与 DeepAgents 默认值一致；未经授权的文件访问及路径穿越仍被拒绝 | Passed |

验证环境为独立 Compose project `yuxi-session-rename`，使用独立端口和全新持久数据槽位，未操作原运行库。镜像内依赖已安装；标准 `uv run --group test` 的同步步骤因系统安装权限失败，实际测试使用 `uv run --no-sync --group test pytest ...`，未更改依赖锁。

独立 Reviewer 已审查完整需求与 `git diff HEAD`，发现并修复消息计算属性的变量遮蔽和 ThreadMessageList 的展示组误命名，复查无阻断问题。工程契约校验、64 项校验器测试、文档链接与构建均通过。真实外部模型 provider 和真实外部模型触发摘要压缩验证 Not run，确定性模型服务不替代外部 provider 兼容性证明。

## 后果

表/列/约束重命名需要增加 business Schema 版本，使旧运行进程明确拒绝新库。独立验证环境需要独立 Compose project、端口和持久数据槽位。若保留旧数据，除关系数据库外还要验证 checkpoint、JSON 快照、摘要路径和 workspace 产物，不能只依据 DDL 成功宣告迁移完成。

Session 生命周期状态当前包括 active/archived/subagent 等，而运行状态来自 Turn/Run。OpenAI Agents API 的 idle/in_progress/requires_action/failed 描述执行状态，直接替换业务生命周期会改变列表、授权及归档语义。更清晰的状态投影、会话级配置快照和按 Turn 展示可以另行提出，不默认并入改名。

参考 [Agents API Session 与 Turn](https://developers.openai.com/api/docs/guides/agents-api/sessions)、[AgentSession 类型](https://developers.openai.com/api/reference/python/resources/beta/subresources/agents/subresources/sessions/methods/create) 与 [Agents SDK 多轮状态策略](https://developers.openai.com/api/docs/guides/agents/running-agents)。Agents API Session 聚合配置、对话和长期工作，SDK Session 提供由应用存储的历史连续性；本决定借鉴职责划分，保留 Yuxi 的 PostgreSQL 与 LangGraph 事实 Owner。
