# Run 消息记录统一入口

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/message_recorder.py

## 问题

同一 Run 的模型与工具生命周期重复维护执行身份、时间校验、计时和短事务。工具 Command 结果在消息记录与公开适配两处提取，调用方需要理解多个入口的处理顺序。

## 决策

### 实现方案

RunMessageRecorder 消费原生 messages/tools ProtocolEvent，分别保存模型增量聚合与工具计时状态，共用序号、时间戳与单调耗时逻辑。生命周期事实由现有 Model/Tool Repository 在独立短事务中保存；增量 token 只在内存聚合。tool-finished 的 Command 在记录器中提取为相同 call_id 的 JSON 结果，提交后返回相同协议结构的事件供 OpenAIEventAdapter 公开投影。源事件及其 seq、timestamp、namespace 保持不变。execution 使用记录器返回值，不再次投影原始 Command。

ModelMessageAuditRepository 与 ToolMessageAuditRepository 拥有幂等、归属、工具声明和恢复来源约束；PublicItemRepository 拥有公开身份与索引。消息事实先提交，公开快照再独立提交，之后发布 SSE。公开投影拒绝保留已提交的工具结果。锁序继续遵循[工具审计事务决定](2026-09-30-tool-audit-thread-lock-order.md)。裸 tool-error 只记录错误观察，checkpoint 对账和 Run owning transaction 继续裁决失败或中断。

Langfuse callbacks、Debug 审计接口与混合展示、Schema、公开协议和 Run 终态收敛保持现有边界。本决定只统一生命周期消息记录，不新增逐条原始事件存储或回放系统；公开协议和恢复行为继续由[原生事件与 Agents API 决策](2026-09-30-langgraph-agents-events.md)拥有。

## 替代方案

- 保留两个采集器：公共生命周期实现和工具结果提取继续重复。
- 仅增加转发包装器：新增一层入口，保留原有维护表面。
- 合并所有 Repository 或引入通用事件持久化接口：工具声明、resume 来源链和公开索引的不同约束被隐藏在通用分支中。
- 合并消息事实与公开快照事务：公开投影失败可回滚已观察到的工具完成事实，并改变现有锁序与失败边界。
- 完整保存每条原始 event：引入事件日志和回放职责，超出降低现有实现心智负担的目标。

## 后果

执行入口只维护一个消息记录器，模型和工具保留各自的领域状态与持久化方法。公开适配入口接收已经规整的工具结果，原始 Command 的解释责任属于消息记录器。记录器与公开适配器仍有独立的聚合和展示状态，各自对应持久事实与公开内容块职责。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 同一记录器保持模型与工具记录、时间和 usage | 合并后状态串用或遗漏事件 | message_recorder / Model、Tool repositories | 相关 unit；真实 PostgreSQL 回读 Message 与 ToolCall | 交错来源、重复 start、缺序号与非法输入 | Passed |
| Command 结果只提取一次，消息记录与公开输出保持相同调用身份 | 源事件被修改、相邻结果误关联 | message_recorder / execution / openai_events | 规整结果 unit、实际执行循环负控、PG 公开快照回读 | 不匹配 call_id、遗漏返回值、公开投影拒绝 | Passed |
| Debug 序列化与 Langfuse callback 装配保持既有逻辑 | 工具 DTO 或回调丢失 | messages / runtime / tracing | 相关 unit；Langfuse 身份真实 PG integration；源码核对 | foreign Thread、错误 owner 与中断 | Passed |
| 真实 HTTP、worker、SSE、刷新与恢复保持一致 | 用户链路组装或运行环境差异 | execution / public_items / worker | 既有 HTTP integration 与生命周期 E2E | 取消、审批恢复、跨 Thread | Not run |

实际命令与范围：

- `tmp/events/compose exec -T api uv run --group test pytest test/unit/services/test_model_message_audit_service.py test/unit/services/test_tool_message_audit_service.py test/unit/services/test_run_message_recorder.py test/unit/services/test_openai_event_adapter.py test/unit/services/test_chat_service_langfuse_stream.py test/unit/agents/test_base_tool_event_normalize.py test/unit/services/test_base_agent_langfuse_config.py test/unit/services/test_conversation_message_audits.py test/unit/services/test_chat_service_sync.py -q`：101 passed。
- `docker compose exec -T api uv run --group test pytest test/unit -m "not slow" -q`：在运行测试前因现有 `yuxi.egg-info` 写权限失败。使用已安装依赖执行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：2416 passed、55 skipped；skip 不作为产品链路通过证据。
- `tmp/events/test test/integration/services/test_agent_run_lease.py --confcutdir=test/integration/services -k 'message_recorder or model_audit or tool_audit or langfuse_identity' -q`：4 passed。此集合直接使用真实 PostgreSQL 与自身资源清理，confcutdir 排除需要不可用 HTTP API 的上层共享清理 fixture；没有替代 repository 或数据约束。新增用例回读已提交模型、工具、唯一 ToolCall 和公开快照，验证公开写入拒绝后工具仍 completed，以及错误 owner 不能覆盖。
- 同一 integration 文件直接 PG 全集：6 passed、2 failed。未修改的 lease 恢复测试引用当前 runner 已不存在的 `reconcile_pending_runtime_cleanups`；未修改的子 Run 测试构造的 pending Run 不满足运行环境的 `ck_agent_runs_nonterminal_shape`。两项不属于记录器变更范围，保留失败，不计入通过。
- 不指定 confcutdir 的 integration：8 个 setup error，API 登录超时。API/worker 重载后报告运行数据库 business=11、当前工作树 required=12；HTTP/E2E 未执行，不修改已有数据库或版本记录来绕过启动校验。当前工作树另有未提交的生命周期与 Schema 改动，本决定只覆盖记录器整合增量。
- 负控：临时让 execution 忽略记录器返回值，`test_execution_forwards_recorded_command_result_to_public_adapter` 因原始 Command 进入公开 JSON 转换而失败；恢复后同一测试通过。工具输出、调用身份和完成结果作为 oracle，未以 mock 调用次数替代结果。
- `node --test frontend/test/unit/messageDebug.test.js`：35 passed，覆盖审计与实时消息合并、时间与 Langfuse URL 投影；未修改前端实现。
- Ruff 检查、工程信任检查、64 项 verifier 单测、文档 build 与 `git diff --check` 通过。默认容器的 Ruff cache 无写权限，使用本地已安装 Ruff 检查受影响文件。

外部真实 Langfuse 导出与真实模型 provider 未验证；Langfuse callbacks 和远端导出实现未修改。

旧能力不存在：源码和测试中无 ModelMessageAuditCollector、ToolMessageAuditCollector 及其独立 service 模块；公开适配器不再调用 tool_event_output。相关领域 Repository 保留。

重新引入条件：新的真实 consumer 需要独立生命周期记录边界时，单独评估职责与证据，不为当前模型和工具路径恢复转发层。
