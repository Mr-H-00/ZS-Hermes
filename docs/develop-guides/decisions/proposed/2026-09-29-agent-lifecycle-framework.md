# Agent 生命周期执行链收敛方案

状态：proposed
类型：simplification
Owner：backend/yuxi/modules/agents/services/execution.py

本文面向维护 Agent API、worker、前端和持久化的开发者。[已实施的生命周期决策](../implemented/2026-09-29-agent-lifecycle-framework.md)拥有当前 Thread → Turn → Run 模型及 Public 协议事实。本文对照原提案，记录执行链收敛的完成情况与待验收边界；下文的“已完成”表示代码已具备该能力，具体证据和未执行范围另行标明。

## 问题

Thread → Turn → Run 主链和本提案的服务收拢已经落到当前工作树。新增边界仍需核对真实 PostgreSQL、worker、跨 Run SSE 与 Web 消费，尤其是 checkpoint 独立写入与 Message/Run/Turn 业务事务在故障时的结果。CLI 完整主链路按用户要求暂缓验证；完成状态必须区分代码迁移、定向单测与未执行的真实链路验收。

### 原方案事项对照

本表对照原提案的第 0–6 阶段。每项按当前代码和已有证据标注；尚缺的交付验收与下文新增收敛工作分别列出。

| 原阶段与事项 | 当前情况及补充 |
|---|---|
| 0. 验证工具批次 checkpoint、无工具接管、等待取消、子执行归属 | （已完成，真实 checkpoint 边界、等待清理和父子 Run 归属已有集成及 worker E2E 证据；原 `test_agent_steer_e2e.py` 已由生命周期 E2E 覆盖。） |
| 1. 建立 Input/Receipt、Thread/Turn/Run Schema、原语与锁 | （已完成，Schema v10、作用域幂等、关系约束及真实 PostgreSQL 并发测试已落地。） |
| 2. follow-up FIFO、多 steer 聚合、批次冻结、领取时创建 Turn、提交后投递 | （已完成，`agents/inputs.py` 与 `agents/scheduler.py` 负责接入和领取；HTTP/PG 与 worker 链路已有回读证据。） |
| 3. yielded、waiting、resume、cancel、暂停/继续及失联 owner 收敛 | （已完成，Turn/Run 用例和 worker lease 已承接状态转换；等待恢复、取消及队列暂停已有 E2E 覆盖。） |
| 4. Input/Turn/Run 快照、跨 Run SSE 与 Langfuse Turn 观察 | （已完成，`agents/events.py` 用 Redis 增量与 PostgreSQL 快照续流，终态从持久事实投影；Langfuse 以 Turn 关联多段 Run。） |
| 5. Web、CLI、定时与内部调用转向 Public/统一用例 | （部分完成，Web 对话与 CLI 代码走 Public Thread，定时调用复用领域用例，旧对话入口不再注册；CLI 完整主链路验收按用户要求暂缓，本轮不继续测 CLI。） |
| 6. 移除 Request 及旧兼容代码，同步规范、启动和交付 | （部分完成，Request/Invocation 业务入口和表已移除，Session 仅为 Thread 协议别名，Schema readiness、规范及新执行链收拢已更新；最终门禁与独立 Review 尚未完成。） |

原方案删除清单也需按边界判断：

| 原删除事项 | 当前情况及补充 |
|---|---|
| Request 业务实体、状态及结果查询 | （已完成，Input/Receipt、Turn/Run 分别拥有接收和执行事实；其他领域的 HTTP `request_id` 不属于 AgentRunRequest。） |
| `public_api.py` 的混合生命周期 | （已完成，Public 路由和 `agents/` 用例已承接职责。） |
| `session_input`、`session_events` 与平铺生命周期服务 | （已完成，真实职责已分别迁至 `agents/` 用例、执行器及子执行服务，旧模块和生产导入已删除。） |
| Request 队列与预建 Turn | （已完成，待处理项是 Input，领取 FIFO 队头时创建 Turn。） |
| 单 pending steer 拒绝逻辑 | （已完成，同 Turn 的多次 steer 聚合并在安全边界冻结批次。） |
| 用 Request ID 表达 trace、Turn 或恢复身份 | （已完成，Turn、Run 与等待点各有明确身份和结果关联。） |
| Invocation 与 Agent eval 专用执行入口 | （已完成，专用对话路由已移除；评估调用方使用 Public Thread。） |
| 非 Public Agent 对话及旧 Request 写入口 | （已完成，Public Thread 为主入口；保留的 `/api/agent` 属于 Agent 配置管理。） |
| SSE 字符串二次加工 | （已完成，Public SSE 按结构化事件编码并从持久事实投影终态；执行器到 worker 直接传递结构化增量和最终 checkpoint 结果。） |

原提案的验收主张按现有证据进一步拆开；“部分完成”指行为已有实现，但原文要求的某个负向或交付证据尚未闭合。

| 原验收事项 | 当前情况及补充 |
|---|---|
| 输入持久接收且幂等 | （已完成；当前接收事务只写 Receipt/Input，Message 延迟到消费事务，见[输入投影分离](../implemented/2026-10-10-input-message-projection.md)。） |
| 执行配置在接收时冻结 | （已完成，Input 保存模型和审批模式快照，领取与幂等重放不重新读取默认值。） |
| follow-up 领取时才创建 Turn | （已完成，真实 PG 竞争测试核对 FIFO 队头与 Turn/Run 创建。） |
| 多 steer 聚合并保持消息顺序 | （部分完成，聚合、顺序及 pending 唯一约束已有测试；“领取同时追加”的独立并发 oracle 未确认。） |
| 只在安全边界接管 | （已完成，真实 checkpoint 与 worker 链路核对工具批次和无工具路径。） |
| 无 steer 的工具循环维持同 Run | （已完成，生命周期 E2E 核对普通工具循环不额外切 Run。） |
| waiting 禁止普通消息，恢复同 Turn | （已完成，HTTP 拒绝、等待点一次性消费及浏览器等待交互已有覆盖。） |
| 取消撤销本轮 steer，暂停并保留 follow-up | （部分完成，取消、暂停和等待清理已有测试；清理崩溃后的完整 worker 重入尚未单独验收。） |
| Turn/Run 与结果原子收敛 | （部分完成，有效 lease 下的业务事务和错绑拒绝已实现；checkpoint 独立写入及终态故障注入还需按新执行契约验证。） |
| SSE 跨 Run 恢复整轮 | （已完成，真实 SSE 对 Redis 过期、resync 和 PostgreSQL 终态已有覆盖。） |
| Langfuse 与前端统一 Turn | （已完成，Turn 根观察及 Web Turn 展示已有测试；外部观测不可用仍不改变 PostgreSQL 结果。） |
| 权限覆盖所有接入与查询 | （部分完成，JWT/Key/APP 作用域与 repository 拒绝已有测试；未绑定 full Key 的完整 HTTP 主链路尚未独立复验。） |
| 新 Schema 与消费方一致 | （部分完成，Schema readiness、Web/Public 和迁移代码已落地；最终交付门禁待执行。） |
| Agent 对话仅通过 Public，删除 Invocation/eval 专用链路 | （部分完成，旧路由未注册且代码消费者已迁移；CLI 完整主链路按用户要求暂缓验证。） |
| Session 仅为 Thread 协议别名 | （已完成，Thread/Session 共享领域用例，交叉读取、幂等及作用域有 HTTP 测试。） |
| Thread 仅提供归档 | （已完成，旧删除路径移除，Project 级联归档和在途输入检查有真实 HTTP/PG 证据。） |
| Request 业务层与旧兼容入口消失 | （已完成，旧表、路由和导入已移除；其他领域保留的 `request_id` 不构成 Agent Request。） |

## 提案

### 实现方案

按同一执行链收口，不改变 Thread → Turn → Run、Input 状态、Public wire 契约或 Schema，不增加通用事件总线、业务状态或旧模块转发壳。

1. **固定输入和输出契约。** 普通输入继续使用 `AgentRunInputMessage` 的有序消息语义，类型与规范化位于 `services/agents/input_messages.py`；恢复回答和审批仍是绑定等待点的控制输入，不进入普通 FIFO。执行器向 worker 直接交付结构化增量和带最终 checkpoint 的终结结果。LangGraph checkpoint 由 PostgreSQL checkpointer 独立写入；有效 lease 下的业务事务负责 Message、输出指针、Run 和 Turn 结果。两种 PostgreSQL 写入边界不能宣称为同一原子事务；缺失所需 checkpoint 时不得宣告成功。（已完成，输入类型、结构化交接和缺失 checkpoint 拒绝已有定向单测；故障下的真实 PostgreSQL 回读待验收。）
2. **收拢执行准备与服务职责。** worker 取得 lease 后在 `services/agents/preparation.py` 同次准备 Context 与 manifest；接收时模型与审批模式由 `agents/input_config.py` 冻结。子 Run 结果/取消归 `subagent_run_service.py`，ARQ 投递归 `agents/transport.py`；旧 `agent_run_manifest_service.py` 与 `agent_run_service.py` 已删除。（已完成，相关 96 项定向单测及静态导入检查通过。）
3. **统一 chat/resume 执行循环。** `agents/execution.py` 用一条图事件循环处理普通输入和等待点恢复，向 worker 交付结构化增量与终结结果；worker 持有 lease/heartbeat、检查取消并收敛失联。Redis 只保存短期增量、取消信号和投递；SSE 在跨 Run 续流及终态时查询 PostgreSQL，过期增量发出明确 resync。（已完成，内部 JSON 字节往返已移除，执行链 189 项定向单测通过；新增真实链路仍待验收。）
4. **迁移旧模块的剩余职责。** 图执行归 `agents/execution.py`，消息对账和审计归 `agents/messages.py`，checkpoint 状态读取归 `agents/state.py`，历史/搜索归 Message/Thread 用例，Redis/ARQ 归 `agents/transport.py`。`chat_service.py`、`conversation_service.py`、`run_queue_service.py` 及其生产导入已删除。（已完成，相关定向单测与生产导入静态搜索通过；跨作用域查询的真实 HTTP 回读待验收。）
5. **逐段验收并交付。** 每段先做符号/依赖静态核对和最小相关 unit；触及持久事务、worker、SSE 或 Web 消费时，用有限的真实 PostgreSQL、worker、跨 Run SSE 和页面证据回读结果。最终运行必要的全量门禁及独立 Review。按用户当前要求不扩展重复 E2E，也不继续测试 CLI；CLI 未补的完整主链路验收如实列为未验证。（部分完成，定向 unit、后端全量 unit、Web lint/unit、工程契约与文档构建通过；新增真实链路回读和独立 Review 尚未完成。）

### 边界与发布点

接收事务提交 Input、Receipt 及附件来源；领取事务创建 Turn、Run 和正式 Message，绑定附件后提交，再投递 ARQ。Receipt 不随消费回填执行归属。worker 取得有效 lease 后准备 Context/manifest，执行器返回结构化增量与最终 checkpoint 信息，worker 校验 owner 并收敛业务结果。Redis 的增量和取消信号不能替代 PostgreSQL 的结果与事件归属。SSE 订阅以持久 Run 顺序跨段恢复，终态从明确的 Turn/Run 关系投影；Run.end 不等于 Turn.completed。

## 替代方案

| 方案 | 收益 | 不采用的原因或代价 |
|---|---|---|
| 保留双 chat/resume 循环和内部 JSON 字节流 | 当前改动少 | 重复编码、解析和结果收敛继续增加执行链维护成本。 |
| 把 checkpoint、Redis 增量和业务结果放入统一事件总线 | 提供统一抽象 | 增加没有当前消费者的状态与失败恢复机制，也无法使独立的数据库写入自动原子化。 |
| 立即删除旧 service 文件 | 目录表面简洁 | 历史、审计、权限和子 Run 调用尚有真实消费者，直接删除会留下行为缺口。 |

选择按消费者迁移，每次只保留一个实际执行入口；服务边界的正确性以运行时调用关系和持久事实为准。

## 验收标准

下表的“当前结果”只指新增执行链收敛的现有证据；`Passed` 为已运行的定向测试，`Inspected` 为静态核对，未执行的真实边界仍在 checklist 中。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 输入类型和有序多消息在所有入口一致 | 图片/文本顺序变化、配置重读、普通消息绕过等待点 | `agents/input_messages.py`、`agents/inputs.py` | 定向 unit 96 passed；真实 HTTP/PG 本轮未跑 | 同键异意图、非法图片/回答、等待时普通输入 | Passed |
| 准备只在有效 lease 后发生，manifest 与 Context 来自同次准备 | 失效 owner 写入配置、准备失败却完成 Run | `agents/preparation.py`、`agents/runs.py` | 定向 unit 96 passed；最小 worker/PG 本轮未跑 | 失效 lease、配置准备抛错 | Passed |
| chat/resume 共用执行循环，传输前不编码 JSON 字节 | 含换行或 JSON 文本的增量被拆、丢失或错绑 | `agents/execution.py`、`run_worker.py` | 执行链定向 unit 189 passed；真实 worker 恢复本轮未跑 | 文本含 JSON/换行、恢复重入、取消竞态 | Passed |
| 最终输出与 Run/Turn 在有效 owner 下同业务事务收敛 | checkpoint 缺失却完成、旧 owner 覆盖或结果错绑 | `agents/messages.py`、`agents/runs.py`、PostgreSQL checkpointer | 真实 PostgreSQL 故障注入并回读 Message/Run/Turn 与 checkpoint | 业务写入后失败回滚、失效 lease、缺失 checkpoint | Inspected |
| Redis 过期和跨 Run 仍能投影正确终态 | 丢失增量伪装成功、Run.end 误结束 Turn | `agents/events.py`、`agents/transport.py`、Public SSE | 定向 SSE/PG 回读；Web 对应状态核对 | 过期游标、断线重连、非法 Run 游标 | Inspected |
| 旧服务及旧入口无生产调用方，Web 使用 Public | 删除后历史/权限失效、Web 调旧接口 | Thread/Message 用例、repository、Web | 生产 import/路由静态搜索，相关 unit 与 Web lint/unit | 跨作用域历史/审计、旧入口重新注册 | Inspected |

旧能力不存在：`chat_service.py`、`conversation_service.py`、`run_queue_service.py`、`agent_run_manifest_service.py`、`agent_run_service.py` 及输入旧模块均已删除，没有执行器到 worker 的 JSON 字节往返；原 Request/Invocation 业务入口继续不存在。重新引入条件：出现当前用例无法表达的明确消费者，且先确定其事实 Owner、失败边界和独立验收证据。

## 风险

1. LangGraph checkpoint 与业务结果分别由 PostgreSQL 写入，无法声称跨两者原子提交。执行器必须在确认所需 checkpoint 已持久化后交付终结结果；worker 仍按 lease 和持久状态处理失联。
2. 历史、搜索、审计、权限和子 Run 消费者已迁移；静态搜索不能证明真实 HTTP 作用域和故障路径，仍需按风险做有限回读。
3. Redis 增量可能过期。SSE 必须明确 resync，并以 PostgreSQL 的 Turn/Run/Message 事实投影终态，不能合成丢失的 delta。
4. 用户要求控制 E2E 范围并暂停 CLI 测试。新执行链尚未验证的真实边界必须在交付记录中标明，静态和 unit 结果不替代持久结果回读。

## Checklist

### 已完成

- [x] Thread → Turn → Run、持久 Input/Receipt、FIFO 领取、多 steer 聚合和提交后投递。
- [x] worker lease/取消/失联收敛、等待恢复、Turn/Run 持久结果与 PostgreSQL checkpoint 使用。
- [x] Public Thread 主协议、Session 命名别名、Web Turn 展示、跨 Run SSE 和 PostgreSQL 终态投影。
- [x] 原 Request/Invocation 业务入口、旧对话路由和独立 Agent eval 执行链删除；CLI 调用代码已转 Public。
- [x] 统一输入消息类型、接收时配置冻结，以及有效 lease 下的 Message/Run/Turn 业务结果事务已有实现。
- [x] 将输入类型和规范化移入 `agents/input_messages.py`，固定结构化增量与最终 checkpoint 交接。
- [x] 将 worker 时的 Context/manifest 准备移入 `agents/preparation.py`，拆散 `agent_run_service.py` 的剩余职责。
- [x] 合并 chat/resume 执行循环，移除执行器与 worker 之间的 JSON 字节往返。
- [x] 迁移 `chat_service.py`、`conversation_service.py` 的真实消费者后删除旧模块，将 Redis 存取归入传输模块。
- [x] 修正图片 init 值、子 Run 结构化进度、中断解析失败收敛及异常输出事务；同 Session 锁定读取刷新 lease 事实，相关负向 unit 通过。
- [x] 后端全量 unit 2360 passed、Web lint/unit 380 passed、工程契约及其 63 项单测和文档构建通过。

### 未完成

- [ ] 以最小真实 PostgreSQL/worker/跨 Run SSE 与 Web 页面回读新增边界；静态及 unit 证据不能代替该结果。
- [ ] 后续独立 Review 继续核对完整需求、diff、测试与规范；本轮已修复已发现的功能缺口，不扩大 Review 范围。
- [ ] CLI 完整主链路补充验证暂缓；本轮不继续测试 CLI，交付时明确未验证范围。
