# 移除 Agent 状态中的文件镜像字段

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/services/execution.py

## 问题

Agent 状态向 HTTP 与 SSE 调用方返回 `files`，但当前 Agent state 没有该字段的写入者，前端文件面板通过真实文件系统接口读取 Workdir。文件提及候选和已上传路径列表仍读取该字段，但当前 runtime 不产生这份文件镜像。

## 决策

### 实现方案

从 `AgentStatePayload` 和 `extract_agent_state` 移除 `files`。HTTP 状态查询与运行 SSE 共用该投影，只返回待办、产物、子 Run 和用量。删除前端提及候选与已上传路径列表的状态字段读取及 composable 参数，附件候选继续来自持久附件元数据。同步清理测试中的空文件镜像和运行时说明。

文件字节继续由 Workdir/Sandbox 边界拥有；不改附件、文件浏览、产物、数据库 Schema 和历史 checkpoint。带有旧字段的 checkpoint 经同一投影读取后不外发该字段。

## 替代方案

- 保留：继续维护没有消费用途的公开字段。
- 缩小：始终返回空字典，仍然保留误导性的契约。
- 替换：用真实文件树填充，会复制文件系统接口的职责和读取成本。
- 删除：去掉字段与投影，使用现有文件系统接口，采用此方案。

## 后果

HTTP 与 SSE 调用方不再收到 `agent_state.files`。前端文件候选与附件列表只读取附件元数据，真实文件检索继续调用文件系统接口。此删除不包含数据迁移和兼容层。

## 验证

- `docker compose exec -T api uv run --no-sync pytest test/unit/services/test_checkpoint_state_reader.py test/unit/services/test_chat_service_langfuse_stream.py -q`：Passed，37 passed；非空旧字段不会出现在 HTTP 投影和实时/最终状态 chunk。
- `docker compose exec -T api uv run --no-sync pytest test/integration/api/test_checkpoint_state_view.py -q`：Passed，1 passed；真实 PostgreSQL/HTTP 的空状态与保存状态均只返回保留字段。
- `docker compose exec -T api uv run --group test --no-sync pytest test/unit -m 'not slow' -q`：Passed，2388 passed、55 skipped；跳过项依赖容器未挂载的仓库配置。默认同步命令在构建 editable package 时因 `yuxi.egg-info` 权限失败，使用容器现有依赖运行。
- `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run test:unit`、`docker compose exec -T frontend pnpm run build`：Passed，386 个前端测试通过；附件候选保留元数据、按路径去重并响应附件更新。
- `playwright-cli -s=agent-files-removal run-code --filename=/tmp/agent-files-browser.js`：Passed；真实上传并确认测试附件，读取 HTTP 状态没有文件镜像，DOM 的附件列表与文件提及候选保留测试附件。截图仅包含测试文件候选，测试资源已清理。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`cd docs && pnpm run build`、`git diff --check`：Passed。
- 额外执行 `docker compose exec -T api uv run --no-sync pytest test/e2e/test_agent_lifecycle_e2e.py::test_first_input_and_follow_up_fifo_cross_worker -q`：失败；未启动确定性模型重放服务导致连接错误和清理超时。测试 Turn 已经通过真实 API 取消，所属 Project/Agent 已清理；不将该项计为通过。

旧能力不存在：运行时类型和状态投影、前端状态读取、CLI 与当前机制说明均无文件镜像的生产或消费入口；相关旧字段仅作为删除负向测试输入保留，历史发布记录保持原文。

重新引入条件：存在明确的当前消费者，且文件状态的 Owner、隔离、更新语义和文件系统接口之间的职责已经确定。
