# 输入接收与执行消息投影分离

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/agents/services/inputs.py

## 问题

接收时创建 Message 使排队输入进入正式历史，接收顺序与引导消费顺序不同会污染执行时间线。唯一 pending steer 节点还把多个提交合并成一个身份，妨碍逐条取消、附件归属与排队转引导。

## 决策

### 实现方案

PostgreSQL AgentInput 永久保存每次提交的有序原始消息和冻结配置。接收事务只创建 Input、Receipt 并绑定附件；Message 在消费事务内生成，保留 source_input_id、input_position 和 received_at，created_at 表示投影时间。移除 AgentInputMessage、cutoff_seq、唯一 pending steer 和 Run.input_id，Run 的 input_ids 从消费关系按接收序号查询。提交级附件与不可变 Receipt 的后续收敛由[附件简化决定](2026-10-10-input-attachment-simplification.md)拥有，取代本决定初版的逐消息文件引用及回执消费回填。

Session 行锁串行化领取、取消、转换和接收。follow-up 单条领取；steer 按接收序号领取附件就绪的连续前缀。消费事务同时创建 Turn/Run、消息、附件最终关联和消费归属，提交后投递 ARQ。接续 Run 沿用当前配置，空闲批次使用首个 Input 配置和来源。消息来源和执行范围由数据库约束；重试不重复生成消息。

新增幂等 input.promote 控制事件，pending follow-up 原地转 steer，保持身份、正文和顺序。等待用户/审批、协作等待、取消清理及附件未就绪时拒绝；暂停只允许调整优先级。取消仅改变选中的 pending Input，保留原始内容和附件归属。前端从 Input 渲染待处理队列，仅将真实 Message 投影加入正式历史；普通发送与直接引导共享图片与附件校验。

业务 Schema 升版并撤下旧升级入口，旧库明确拒绝启动。采用独立 worktree、新槽位和新数据库验证，不迁移、清空或重排现有用户数据；旧协议不兼容。普通发送默认排队，停止交互不扩展。

本决定初版使用 Schema 7，后续附件简化升至 Schema 8。本决定更新[优先输入队列](2026-10-01-thread-priority-input-queue.md)中的唯一 steer、接收合并和整批取消策略，以及[附件事实归一](2026-10-09-agents-owner-simplification.md)中的 Receipt 关联和 Schema 升级边界。当前机制由[输入队列与调度](../../../mechanisms/agent-request-queue.md)说明，公开协议由[Agents API](../../../advanced/agents-public-api.md)说明。Input 内部配置快照只用于持久审计与执行，公开 Input/queue 响应不返回系统提示词或管理员摘要配置。

## 替代方案

- 接收即保存 Message 再调整时间/过滤状态：正式消息仍同时承担队列和执行审计两种职责，拒绝。
- Redis 保存待消费正文：可靠性、恢复与幂等需另建持久化边界，拒绝。
- 保留唯一 steer 并追加成员：无法自然逐条转换/取消；改为独立 Input、消费时合批。
- 消费后删除原始 Input 正文：丢失原始配置和输入证据，拒绝。

## 后果

这是业务 Schema 和公开协议的破坏性变更，Web、CLI、Demo、协作身份和 SSE 消费方同时采用新契约。保留原始输入增加文本和图片存储量，消费消息的事务更大，文件准备仍在消费事务外完成。附件取消后仍归属原 Input，不能重新提交到另一条消息；删除输入不等于释放文件。持续引导仍可推迟普通 FIFO。

Session 锁拥有领取、取消与转换的竞争边界，附件锁按整个提交的文件 ID 排序，避免逐消息反向绑定死锁。安全接管重新检查当前优先队头是否 ready；已就绪队头被取消后暴露未就绪队头时，原 Run 继续执行。领取后进程失联仍由既有 lease 收敛为明确失败并暂停队列，不自动重复已经执行的副作用。

## 验证

以下保留 Schema 7 的历史证据，不作为后续附件简化与 Schema 8 的验收结果。

证据使用独立 Compose 槽位、新 PostgreSQL/Redis/对象存储和工作目录；未修改原开发数据库。测试容器复用已安装的依赖，使用 `python -m pytest`，不把 editable 包写权限导致的普通 uv 同步失败计为通过。共享清理器的 HTTP/E2E 按组串行验收，交叉清理中断的运行不计通过。模型和解析服务使用确定性协议 oracle，不代表外部模型能力通过。

- 后端非 slow unit：`python -m pytest test/unit -m "not slow" --tb=short`，2628 passed、55 skipped。前端 `node --test --test-concurrency=1 "test/**/*.test.js" "test/**/*.spec.js"`，533 passed；ESLint、TypeScript 和 Vite build 通过。
- 真实 PostgreSQL/Redis：输入 Schema、并发接收、优先调度、附件、租约、ARQ、协作、定时任务、Schema 版本和关联约束集合 142 passed。数据库回读证明接收无 Message、消费原子绑定、取消无投影、来源唯一、配置保留和旧 Schema 拒绝；包含回滚、越权、等待状态、队头未就绪及反向附件绑定负向案例。
- HTTP 的 Session、Items、权限、文件与正式调试来源投影集合最终一次运行 17 passed，包括内部配置泄露负向断言。分页和搜索仅从正式 Message 查询，Input 的 pending/cancelled items 为空。
- 生命周期、扩展工具审计、协作、API Key、SSE 与附件的 32 个 E2E 场景分组通过；审计角色与确定性角色选择夹具修正后分别重跑通过，不将初次失败算作成功。A/B/C/D/E 场景回读旧 Run 输出先于引导消息，D/E 同一接续 Run，B/C 后续分别执行。另有配置冻结 1 项、进程故障 3 项通过，共 36 个不同场景。
- `test_input_projection_recovery_e2e.py` 使用真实独立子进程在消费事务提交后退出；单 Input 两条消息及两个 steer Input 合批都由长期 worker 补投同一 Run。领取租约后退出则恢复为 `worker_lease_expired`。回读 Input 原文、Message ID/位置/时间、Run 数与 attempt 数，确认没有重复投影或结果错绑。
- 实际 Vite 页面配合 HTTP replay 验证逐条转换/取消、图片直接引导、消费后刷新去重及 390px 布局；`inputQueueLive.js` 连接真实 API、数据库与 worker，验证两次页面发送、B 转引导、C 取消、刷新和正式 Message 回读。桌面、移动和真实消费前后截图保留为本地验证产物，不提交测试数据。
- 文档附件的解析、发送、取消与完整资源目录回读 E2E 1 passed；同文件中的知识库 worker 场景因隔离槽位未运行 Milvus，创建数据库返回连接失败，不计通过。外部视觉/非视觉模型探针因未配置模型而显式跳过 2 项。
- CLI 15 tests、typecheck 与 pack dry-run 通过；Demo unit、lint/build 及 18 项浏览器测试通过。独立 Reviewer 不继承实现上下文，核对完整需求、diff、约束、决策与实际证据；发现的竞争、公开配置泄露和测试协议偏差均修正。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts` 的 64 项测试、文档相对链接及 VitePress build、相关 Ruff 和 `git diff --check` 通过。

数据库集合的实际命令为槽位 Compose 中执行 `python -m pytest test/integration/services/test_agent_input_schema.py test/integration/services/test_agent_input_concurrency.py test/integration/services/test_thread_priority_inputs.py test/integration/services/test_input_attachments.py test/integration/services/test_agent_run_lease.py test/integration/services/test_arq_worker_dispatch.py test/integration/services/test_session_cooperation.py test/integration/services/test_scheduled_agent_repository.py test/integration/services/test_schema_migration_version.py test/integration/services/test_database_relation_integrity.py --tb=short`。E2E 使用同一入口，选择 `test_agent_lifecycle_e2e.py`、`test_agent_lifecycle_extended_e2e.py`、`test_session_cooperation_e2e.py`、`test_agent_lifecycle_key_scope_e2e.py`、`test_openai_events_e2e.py`、`test_draft_attachments_e2e.py`、`test_session_config_e2e.py` 和 `test_input_projection_recovery_e2e.py`；不是全目录一次通过。

未验证范围：未执行外部视觉模型与外部 OCR 探针，也不提供历史数据库升级或迁移。隔离 PostgreSQL 在主要压力验证期间临时关闭 fsync，结束时恢复并回读为 on；这些结果证明事务、进程故障恢复和业务关系，不证明宿主机掉电持久性。非 slow unit 的 55 个显式跳过项不计通过。

### 2026-10-11 主干恢复验证

main 遗漏了 `3acb7cae`、`74973129`、`e42e1c80`、`c6b32b73`、`ed908853`、`c373d229`、`86a0262e` 七个已完成提交，导致运行代码要求 business Schema 6，而本地数据库已为 8。按用户确认合并这些提交，保留当前 modules / knowledge-base 命名、项目目录修复和自动标题实现，继续使用现有 Schema 8 数据；没有修改数据库版本标记或执行降版、清库。

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q`：2670 passed、55 skipped。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_thread_priority_inputs.py --confcutdir=test/integration/services -q`：25 passed；真实 PostgreSQL 隔离 Schema，覆盖消费回滚、FIFO/steer、逐条控制和自动标题。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_agent_lifecycle_e2e.py -k 'initial_text_stream_and_saved_items or first_input_and_follow_up_fifo_cross_worker' -q`：首次 3 passed、1 failed，原因是未启动 `test/support/openai_replay_server.py`；启动并确认 health 后单独重跑 `test_public_session_initial_text_stream_and_saved_items[text-False]`，1 passed。四个不同场景经过真实 HTTP、worker 和最终 PostgreSQL/Items 回读，包括 SSE、长输入和 follow-up FIFO；未执行全目录 E2E 或浏览器探针。
- 前端 lint、549 项 unit 和 build，CLI typecheck、15 项 test 和 pack dry-run，Demo lint、13 项 test 和 build，文档 build，以及工程信任检查和 64 项 unit 通过。
- 恢复源码后真实 `/api/system/ready` 返回 200、`degraded=false`，startup、PostgreSQL、Redis 和 worker 均为 ok；独立 Reviewer 审查完整合并 diff，未发现阻塞问题。真实外部模型调用仍未验证。
