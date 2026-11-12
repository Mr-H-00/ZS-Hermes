# storage-migrator 等待 PostgreSQL 恢复完成

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/storage_migration.py

## 问题

PostgreSQL 进程异常退出后会进入 recovery。`pg_isready` 可能已经报告可连接，但迁移器仍可能在获取迁移锁时收到 `database system is in recovery mode`，导致一次性 `storage-migrator` 退出并阻塞 API、worker 启动。

## 决策

Compose 的 PostgreSQL 健康检查同时验证连接可用和 `pg_is_in_recovery()` 为 false。迁移器只在获取 Schema advisory lock 以及持锁后的无副作用可写性探针阶段，对恢复期、启动中和暂时连接失败做有限重试；锁成功后不重放整个迁移流程，所有 Schema、认证和迁移逻辑错误立即失败。探针也拒绝 `transaction_read_only=on`，避免在只读主备节点执行 DDL。

## 替代方案

- 只延长 `depends_on: service_healthy`：不能消除健康检查与实际 DDL 可用之间的窗口。
- 捕获所有 `OperationalError` 重跑迁移：可能重放已执行的迁移副作用并掩盖真实 Schema 错误。
- 让 API 或 worker 自己创建 Schema：破坏一次性迁移 Owner 和启动依赖边界。

## 后果

恢复期最长等待约 55 秒，服务启动会延后但不会把暂时恢复状态记录成永久失败。健康检查和迁移前探针各增加一次只读 PostgreSQL 查询；非恢复期故障仍需要运维处理。迁移器仍是唯一 Schema 修改者。

## 验证

| 验收主张 | 直接证据 | 负向案例 | 当前结果 |
|---|---|---|---|
| PostgreSQL 健康状态不会在 recovery 中提前放行 | `backend/test/unit/config/test_docker_compose_service_boundaries.py` | 缺少 `pg_is_in_recovery()` 的检查契约失败 | Passed |
| 恢复期锁连接失败会有限重试 | `backend/test/unit/services/test_storage_migration.py` | recovery 错误前两次失败、第三次成功 | Passed |
| 持锁后仍处于 recovery 会释放锁并重试 | `backend/test/unit/services/test_storage_migration.py` | `pg_is_in_recovery()=true` 不进入迁移 | Passed |
| 认证错误不被重试或吞掉 | `backend/test/unit/services/test_storage_migration.py` | `password authentication failed` 只尝试一次 | Passed |
| 只读数据库被拒绝 | `backend/test/unit/services/test_storage_migration.py` | `transaction_read_only=on` 不进入迁移 | Passed |
| 迁移与联调回归未受影响 | `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_storage_migration.py test/unit/services/test_wps_development_tunnel.py test/unit/services/test_cloud_drive_protocol.py -q` | 同一测试集合的失败断言 | Passed（46 passed） |

已验证：API 镜像重新构建成功，容器内 `cloudflared 2026.10.0` 可执行；`docker compose up --no-deps --force-recreate storage-migrator` 退出码 0；真实 `/api/system/health` 与 `/api/system/ready` 均返回 200，ready 状态为 `ready`。
