# JobTracker 的作业登记、观察与取消边界

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/background_jobs/service.py

## 问题

任务使用者需要登记后台动作、保存进度、查询执行方上报的结果并请求取消。登记与观察服务承担 Handler 执行、heartbeat、超时及领域终态 Hook，会把执行管理和业务失败判定混入后台作业。本决定面向 JobTracker、worker、知识库与评估模块维护者；当前后台链路由 [ARCHITECTURE.md](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md) 拥有。

## 决策

作业命名、提交/执行目录及不兼容 schema 契约由[后台作业命名与提交、执行边界](2026-10-04-background-job-naming-and-dispatch.md)补充；本文保留登记、观察和合作式取消职责决定。

### 实现方案

`modules/background_jobs/service.py` 提供持久登记、有效执行方的进度和结果上报、查询与取消请求。`modules/background_jobs/dispatch.py` 拥有注册元数据、执行预算和提交后投递；`modules/background_jobs/registry.py` 保持领域 Handler 惰性加载；`workers/background_jobs.py` 和 `background_job_context.py` 拥有执行、lease、超时、取消检查和领域 Hook 适配。业务 service 在 owning transaction 内登记 BackgroundJob 和业务资源，提交后投递 job_id。首次发布失败的 pending 记录由现有 worker publisher 补发，失联执行由 worker 收敛明确结局。

JobTracker 保存和展示执行方报告的状态，不根据单项失败数、进度 100 或异常内容判断业务成败。知识库导入与批量解析、索引的既有失败判定由各自领域 service 保持；重试、补偿与业务恢复同样由领域拥有。共享 PostgreSQL repository 保留 claim、lease、owner 与终态事务保护，执行用例调用这些原语，观察用例不调用领域 Hook。

取消采用合作式协议。JobTracker 只持久化 `cancel_requested`，pending 任务由执行器确认无需执行，running 任务由 Handler 在安全检查点响应。heartbeat 持续维护执行权，用户取消请求不强制中断领域协程；执行超时或失权仍由执行器控制。确认之前状态保持 pending 或 running，取消与结束使用行锁形成唯一终态，终态拒绝迟到进度。

后台作业使用 `pending / running / success / failed / cancelled` 状态；表名、Schema 版本及旧库拒绝行为由后台作业命名决策拥有。管理 HTTP 只提供查询、取消和删除终态记录，授权仍由后端管理员依赖执行。普通用户通过知识库或评估资源读取业务结果。API 不开放创建或任意进度、终态上报。

详情 DTO 对历史和新记录统一投影资源引用、有限计数、数值指标及最多 200 条文件引用，隐藏 payload、执行元数据、完整构建参数、文件正文与原始错误。内部结果继续供领域 Hook 使用。前端提交回执只触发权威详情读取，读取失败保留列表重试资格；账号切换清除待观察回执，迟到请求不能覆盖新会话或更近快照。后台作业按后端终态展示，100% 的 running 任务仍显示运行中。

## 替代方案

- 保持完整 后台作业 服务：可保留执行机制，但 JobTracker 同时拥有执行和领域终态收敛，超出登记与观察服务的目标。
- 收窄 JobTracker，交接到现有 worker：采用此方案，复用执行保护，代价是迁移真实调用方与装配入口。
- 新建 TaskManager 或通用 workflow：新增第二公共入口和维护表面，没有当前 consumer 支持。
- 删除执行管理：会使持久记录和业务中间态失去执行 Owner，不能满足现有用户动作的可靠性要求。

## 后果

JobTracker 和 worker 分别拥有登记观察与执行可靠性，知识库和评估模块继续拥有业务结果。执行安全边界决定取消响应时间，取消回执不保证副作用已经停止；未检查取消的动作仍受原有 worker 超时约束。后台作业只读摘要，完整参数与业务结果从对应领域资源或日志查看。作业记录提供查询和取消，旧 Python 执行入口不保留兼容别名；旧库需要按新的 fresh baseline 重建。

## 验证

以下证据记录职责拆分阶段、命名和 Schema 变更之前的验证；命名变更后的当前证据由后台作业命名决策拥有。验证基线为 Conversation → Session 合并后的 `origin/develop/1.0`（`578c6d454d12cf10ba2ef845d1b8343416945ef7`）。验证使用独立 Compose 项目和数据目录、现有已构建镜像与当前源码挂载，未触碰其他开发拓扑。以下是实际执行的命令和结果；Compose 验证使用本地镜像/网络覆盖文件。

| 命令或证据 | 结果与验证范围 |
| --- | --- |
| `docker compose exec -T api uv run --group test pytest test/unit -m "not slow" -q -p no:cacheprovider` | 环境失败：uv 尝试更新只读挂载的 egg-info，未执行测试。 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q -p no:cacheprovider` | 2530 passed、55 skipped；使用镜像现有测试依赖，覆盖登记无 Handler 副作用、有限进度、业务判定和合作式取消。 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_durable_task_repository.py -q -p no:cacheprovider` | 20 passed；真实 PostgreSQL 验证登记事务不可见/回滚、claim 并发、lease 失权、取消与完成竞争、领域 Hook 原子提交及终态迟到写入拒绝。 |
| `docker compose run --rm --no-deps api uv run --no-sync --group test pytest test/integration/services/test_durable_task_worker_path.py::test_prepare_task_with_failed_initial_arq_publication -q -p no:cacheprovider` | 1 passed；API/worker 停止期间故障注入首次 ARQ 发布，回读已提交 Task 与数据集均为 pending。 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_durable_task_worker_path.py::test_shipping_worker_startup_recovers_pending_publication test/integration/services/test_durable_task_worker_path.py::test_shipping_worker_failure_runs_domain_hook -q -p no:cacheprovider` | 重启 API/worker 后 2 passed；真实 Redis/ARQ worker 执行与资源回读证明成功恢复发布及失败 Hook 收敛。准备和回读使用相同 `DURABLE_TASK_GATE_ID`。 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_task_router.py -q -p no:cacheprovider` | 6 passed；真实 HTTP 管理员权限、取消两阶段、历史结果敏感字段/数量限额、无公共上报 API，以及真实上传→任务→解析文件资源回读。 |
| `docker compose exec -T frontend pnpm lint:check`、`pnpm test:unit`、`pnpm build` | 全部通过，446 unit passed；覆盖旧回执、账号切换、首次详情失败恢复与权威状态读取。build 保留仓库现有大 chunk 提示。 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_tasker_behavior.py test/unit/services/test_task_registry.py test/integration/api/test_task_router.py -q -p no:cacheprovider` | 最终 worker 等价简化和结果摘要修改后 29 passed。 |

Ruff 对全部变更 Python 文件检查及格式检查通过。`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（64 passed）、`pnpm --dir docs build`（含相对链接检查）和 `git diff --check` 通过。

浏览器使用真实管理员登录和任务 API、PostgreSQL fixture 回读，验证部分失败摘要保留业务上报 success、100% running 不变为成功、取消受理后保持 running 并隐藏重复取消按钮，执行器确认后显示 cancelled；截图作为本地交付证据。独立 Reviewer 审查完整需求、diff、测试与规范，有限摘要和首次读取失败恢复问题已修复并复查闭合。

旧能力不存在：shipping 搜索与独立 Review 确认 JobTracker 不依赖 ARQ、Handler registry、heartbeat、超时配置或领域 Hook。原 `modules/tasks/queue.py`、`registry.py` 与 service 中的 BackgroundJobContext、process_background_job 已移除；真实调用方、worker 注册、readiness、前端与机制说明使用对应新 Owner，没有平行旧导出。

重新引入条件：只有明确提出 JobTracker 承担执行编排的需求，并给出当前 consumer、执行 Owner、持久化与失败边界及真实链路证据，才重新讨论通用执行能力。

Not run：完整外部 LLM 评估、完整图谱构建、worker 强制 kill 的额外 E2E 和远端 CI。现有 PostgreSQL lease/中断负向案例和真实 worker 发布/成功/失败路径提供本次职责交接证据；未执行的外部业务链路不宣称通过。
