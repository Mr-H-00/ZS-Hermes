# 执行链与图谱来源关系收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/models/inputs.py

## 问题

数据库接受同一知识库中 File 与 Chunk 错配的 Mention，以及跨 Thread 的 Input 消费和 Receipt 引用。KnowledgeFile 的代次字段已拥有构建和发布事实；版本表的快照及 active_version_id 没有独立读取 consumer，却需要创建、查找、切换和清理。

## 决策

### 实现方案

ORM 复合 FK 把 Run 的输入和已消费 Input 绑定所属 Thread 的 Turn，把 Receipt 的 Input 和 Turn 绑定所属 Thread，把 Run 绑定引用的 Turn。Knowledge ORM 把 Mention 的 Chunk/File/KB 绑定同一来源。删除 KnowledgeFileVersion 表、active_version_id 及其维护逻辑；文件的 generation / active_generation / building_generation 表达申请、服务和构建中的代次。保留现有代次发布、outbox 和投影锁。

继续队列用例在原事务内读取已创建 Run，把其 Turn 写入控制回执；接收、消费、worker 和清理沿用现有流程。版本门禁要求 business=1 / knowledge=1 基线；当前版本仅支持全新建库，直接创建当前结构；不提供数据兼容或数据库迁移。

## 替代方案

- 只在 repository 比对：独立 SQL 和替代写入入口仍能留下错配事实。
- 增加统一 owner trigger 或规范化 owner 列：缺少当前写入绕过证据，超出因果关系修复；保留既有作用域授权边界。
- 保留版本表并加强同步约束：没有历史查询 consumer，现有代次字段已满足发布和清理需求；删除重复事实减少维护。

## 后果

接收、消费和检索保持原有语义；文件元数据不再携带无 consumer 的 active_version_id。错配的直接 SQL 写入在 owning transaction 内失败；没有额外 owner trigger、身份表、配置或状态机。当前版本不考虑历史数据兼容或数据库迁移；开发库重建仍需明确的数据操作授权。

## 验证

- PostgreSQL 负向测试拒绝跨 Thread Input 消费、Run 输入和 Receipt 引用、不同 Turn 的 Receipt Run，以及同 KB 错文件的 Entity/Triple Mention；合法写入和文件级联删除回读保持正确。
- PostgreSQL catalog 回读确认版本表和 active_version_id 不存在；发布与清理回归读取文件当前代次和可见 Chunk。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_database_relation_integrity.py test/integration/services/test_knowledge_projection_integrity.py test/integration/services/test_agent_input_schema.py test/integration/services/test_schema_migration_version.py test/integration/services/test_database_access_paths.py -q`：32 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_thread_priority_inputs.py test/integration/services/test_agent_input_concurrency.py -q --tb=short`：24 passed；回读继续回执的 Turn/Run，并验证幂等重放不新增 Run。子 Agent 夹具显式满足已有 shared 约束。
- 独立 Compose fresh 环境执行 `uv run --no-sync --group test pytest test/e2e/test_agent_lifecycle_e2e.py -k 'first_input_and_follow_up_fifo_cross_worker or waiting_turn_requires_complete_answers_and_resumes_same_turn' -q`：2 passed。覆盖真实 HTTP/worker/PG、FIFO 和等待恢复后的同 Turn 结果。
- 独立 fresh 环境执行相同 E2E 文件，筛选 `cancel_waiting_turn_pauses_queue_until_continue or model_rate_limit_failure_preserves_error_and_queue_can_continue`：2 passed；覆盖取消和模型限流失败后的队列继续。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q`：2523 passed / 55 skipped（既有 marker）。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_schema_migration_version.py test/unit/services/test_schema_bootstrap.py -q`：10 passed；两个域的版本号为 1，空库初始化、重复初始化、中断重试与进程只读检查通过。
- 工程信任检查及其 64 项单测通过。生产源码 Ruff 检查通过；测试文件包含既有长 SQL，测试 lint 忽略 E501。
- `pnpm --dir docs run build` 通过，相对链接检查由 VitePress 构建验证。`git diff --check` 通过。
- 规定的 unit 命令去掉 `--no-sync` 时在依赖同步前失败：容器 uv 缓存权限不足；上述 unit 使用现有依赖执行。
- 未验证：真实模型、知识库向量/图外部服务的完整 E2E；外部投影流程保持原实现，本次由 PG 版本发布、清理和 Mention 删除回读覆盖。

旧能力不存在：版本表、active_version_id、is_active 及其维护逻辑全部移除。

重新引入条件：历史版本快照出现明确查询 consumer，且文件代次字段无法满足其需求。
