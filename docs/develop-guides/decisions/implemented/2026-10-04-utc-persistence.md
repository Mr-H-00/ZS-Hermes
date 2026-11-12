# 持久化时间与有限任务状态收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/shared/datetime.py

## 问题

业务模型混用无时区与带时区时间，写入及数据库时钟查询会丢弃 UTC 时区。跨域比较、租约恢复和定时计算因此依赖不同隐含约定。Task 和清理 outbox 的状态也缺少有限值约束，未知状态会逃过恢复或清理扫描。当前版本仅支持全新建库，无需迁移历史时间。

## 决策

### 实现方案

业务 ORM 时间列统一为 DateTime(timezone=True)，默认值、写入和内部比较直接使用 utc_now，删除 utc_now_naive。API Key 日期输入保留解析得到的时区；定时计算使用实际输入瞬间并返回 UTC；协议毫秒时间转换保留 UTC。Task 与知识文件在取得行锁后直接读取 PostgreSQL clock_timestamp，避免把数据库时钟转成无时区值。报表显式使用上海时区归日，不依赖数据库 session 时区。

Task 模型仅接受 pending/running/success/failed/cancelled，清理 outbox 模型仅接受 pending/applied；repository 和 worker 继续拥有既有状态转换。两个 Schema 域保持版本 1，只支持全新建库；不新增兼容层、时间类型包装、迁移或状态机。

## 替代方案

保留无时区 UTC 并在每个跨域比较处转换：需要维护双重约定且容易遗漏。新增通用 ORM 转换类型：隐藏调用方错误并增加不必要的机制。

## 后果

所有持久化时间有明确时区，跨域比较和外部时间转换保持同一瞬间。数据库拒绝未知任务或清理状态。SQLite 逻辑单测使用测试专用 UTC 回读，真实时间语义由独立 PostgreSQL oracle 验证；生产不支持 SQLite。现有数据库不自动改写或重建。

## 验证

- 隔离 PostgreSQL 执行关系、知识投影、Input、Schema、访问路径、优先队列和并发七个现有套件：58 passed。实际 catalog 无无时区时间列；带偏移日期在不同数据库 session 时区下回读为同一瞬间；未知状态在指定 CHECK 上失败，合法状态回读成功。原无时区 projects.updated_at 能触发该负向证据。
- 独立 fresh Compose 执行 `pytest test/integration/services/test_agent_run_lease.py test/integration/services/test_durable_task_repository.py test/integration/api/test_auth_router.py test/integration/api/test_scheduled_agent_api.py test/integration/api/test_dashboard_router.py -q --tb=short`：50 passed；覆盖数据库时钟、锁等待后的租约到期、身份过期与锁定、真实 HTTP 定时及报表。
- 独立 fresh Compose 执行 `uv run --no-sync --group test pytest test/e2e/test_agent_lifecycle_e2e.py -k 'first_input_and_follow_up_fifo_cross_worker or waiting_turn_requires_complete_answers_and_resumes_same_turn or cancel_waiting_turn_pauses_queue_until_continue or model_rate_limit_failure_preserves_error_and_queue_can_continue' -q`：4 passed；验证真实 HTTP/PG/worker 的 FIFO、等待恢复、取消及失败后的继续。
- 独立 fresh 环境执行 `uv run --no-sync --group test pytest test/integration/api/test_chat_router.py::test_thread_message_audits_return_persisted_facts_without_leaking_into_history test/integration/api/test_chat_router.py::test_thread_history_envelope_has_all_runs_and_keeps_viewed_explicit -q --tb=short`：2 passed；固定日期样本显式标注 UTC，历史输出绑定完整 Run/Turn，接口 UTC 时间及历史隔离预期保持不变。
- 定时 unit 用带偏移输入和纽约夏令时切换核对独立预期 UTC 瞬间。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q --tb=short`：2524 passed / 55 skipped（既有 marker）。规定命令不带 --no-sync 时因容器 uv 缓存权限不足在依赖同步阶段失败，以上使用现有依赖。
- 权限测试显式更新两处旧 oracle：共享候选可跨部门、普通管理员提升角色返回 403；原账号管理隔离断言保留。共享语义由[资源权限](../../../mechanisms/resource-permissions.md)拥有，未修改生产授权。
- 工程契约检查及 64 项规则单测、生产 Ruff、文档构建与 git diff --check 通过。
- 未验证：真实模型与知识库外部向量/图服务的完整 E2E。初始化用户脚本通过编译和 Ruff，未向用户数据库执行。

旧能力不存在：无时区 UTC 写入工具和业务时间列，数据库时钟不再转成无时区值。

重新引入条件：明确的外部协议要求无时区本地日期，仅在其 parser 边界处理。
