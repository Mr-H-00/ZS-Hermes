# 数据库 Schema 迁移 Owner 与轻量版本契约

状态：implemented
类型：architecture
Owner：backend/package/yuxi/storage_migration.py

`storage-migrator` 执行数据库 Schema 变更，`PostgresManager` 持久化版本并提供只读兼容校验；API 与 worker 只消费已经完成迁移的 Schema。

## 问题

API 与 worker 在启动时执行建表和 `ensure_*_schema`，多个运行进程可能并发执行相同 DDL。破坏性 SQL 会在每次启动时重复检查，数据库也没有可回读的 Yuxi Schema 版本事实。现有 `storage-migrator` 已经是 Compose 启动门禁，但需要独占 Schema 迁移。

## 决策

现有 `storage-migrator` 是 shipping 拓扑唯一的 Yuxi Schema 修改者，不新增迁移服务或框架。迁移器持有 PostgreSQL session advisory lock，创建 `yuxi_schema_migrations` 版本表，并分别记录 `business` 与 `knowledge` 域。0.7.3 的 business schema 为 v6，接受未版本化安装、当前 v6、0.7.2 的 v2 和早期 0.7.3 开发环境的 v3、v4、v5；knowledge schema 为 v2，接受未版本化安装、当前 v2 和 0.7.2 的 v1。未版本化安装执行当前幂等建表与收敛 SQL；LangGraph checkpoint setup 完成后才记录 business 版本。v2 通过完整收敛 SQL 直接得到当前结构；v3 依次执行 AgentRun Trace 与 Message 审计、Redis 游标删除和阶段时间字段迁移；v4 执行后两步；v5 只增加 `prepared_at` 与 `first_output_at`。每条相邻迁移完成后才发布 v6。当前版本重复运行跳过该域的 Schema DDL，business v1、未来版本和其他未知版本在执行领域 DDL 前明确失败。

迁移器始终迁移并要求 business 与 knowledge 两个域。API 与 worker 不执行建表、Schema 收敛或 checkpoint setup，只校验两个域等于当前程序版本；版本表或任一域缺失、过旧或过新时拒绝启动。Compose 继续使用 `service_completed_successfully` 阻止迁移失败后的运行进程启动。

版本表只表达已完成的 Yuxi Schema revision，不承诺自动回滚。开发期 v3、v4、v5 不形成发布兼容承诺，但作为明确升级来源保留相邻迁移；确认表结构完整后的本地版本标记校正不是 shipping downgrade。破坏性变更必须继续声明数据影响、保持升级幂等并通过真实 PostgreSQL 验证；后续 Schema 变更提升对应域版本并实现受支持的相邻升级路径。

## 替代方案

- 引入 Alembic 和独立 `db-migrator`：当前没有复杂迁移分支需求，会新增依赖、配置和第二个部署服务；拒绝。
- 只把 DDL 移到迁移器但不记录版本：不能避免每次启动重复执行破坏性收敛，也不能让运行进程校验兼容性；拒绝。
- 保留 API 或 worker 的兜底建表：会重新形成多个 Schema Owner，并让错误部署静默修改数据库；拒绝。
- 要求所有迁移支持 downgrade：数据删除和约束收紧无法形成可信无损回滚；采用发布前备份、幂等升级和明确数据影响。

## 后果

- API 与 worker 启动不再竞争 DDL 锁；迁移错误集中在 `storage-migrator`，失败会阻止运行服务启动。
- 当前版本正常重启不再重复执行 Yuxi Schema 收敛 SQL。
- 裸进程启动前必须先运行迁移器；缺失或不兼容版本形成明确启动错误。
- 首次接入时，没有版本记录的已知 legacy/current 数据库执行现有幂等收敛后建立 baseline；现有 Workdir 中间 Schema 检测继续 fail-closed。
- 版本记录与历史文件迁移不是单一数据库事务。中断时版本不推进，Schema SQL和文件迁移依靠既有幂等边界重跑。

## 验证

- `backend/test/unit/services/test_storage_migration.py` 的入口级负向测试证明 business v1 与未来版本在 DDL 前被拒绝，并证明 v2 完整收敛和 v3、v4、v5 的相邻迁移都先于版本发布。
- `backend/test/integration/services/test_schema_migration_version.py` 在真实 PostgreSQL 覆盖 session advisory lock 单赢家、business v2 完整收敛、v3→v4 审计迁移、v4→v5 游标删除、v5→v6 阶段时间字段、knowledge v1→v2、版本缺失/错误拒绝和正确版本回读。
- `docker compose exec -T api uv run --no-sync --no-dev pytest test/integration/services/test_api_key_schema_migration.py -q`：1 passed，既有破坏性业务 Schema 升级保持幂等和数据约束。
- shipping Compose 迁移后必须回读 `business=6`、`knowledge=2`，并确认既有 Task、定时任务表和 AgentRun 事实均保留；API/worker 只在精确版本匹配时进入 ready。
- 运行进程缺失 business 或 knowledge 任一域时拒绝启动；由 migration unit 与真实 PostgreSQL integration 覆盖。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`：通过，61 tests passed；真实 Schema oracle 已接入 `system-tests.yml`。
