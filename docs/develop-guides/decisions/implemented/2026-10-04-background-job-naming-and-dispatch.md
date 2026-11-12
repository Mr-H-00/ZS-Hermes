# 后台作业命名与提交、执行边界

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/background_jobs/service.py

## 问题

Task 容易被理解为包含 AgentRun 的全局任务，workers 中混合 API 提交端与后台执行端进一步模糊进程边界。读者是后端与前端维护者，前置知识是当前 [后台任务链路](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)。目标是名称、目录、数据库、HTTP 和 UI 一致表达后台作业，并保留[登记、观察、合作式取消边界](../implemented/2026-10-04-tasker-observation-and-cancellation.md)。非目标是合并 AgentRun 与后台作业状态、增加工作流或新增执行机制。

## 决策

### 实现方案

使用 BackgroundJob、JobTracker 和 background_jobs 表；modules/background_jobs 拥有模型、repository、观察 service、作业定义与提交投递入口。workers/background_jobs.py 与 workers/background_job_context.py 拥有领取、执行、heartbeat、取消检查、失联收敛和 pending 补发。Handler 元数据由作业定义提供，业务处理器保持在知识库、图谱和评估模块。提交端不导入 workers 模块，ARQ 通知只携带 job_id，投递始终发生在 owning transaction 提交之后。

HTTP 使用 /api/background-jobs，业务回执与资源执行关联使用 job_id，前端模块和“后台作业”页面采用对应名称。数据库更新表、约束、索引与知识文件 owner 关联字段；用户明确允许不兼容的数据模型变更，采用新的 fresh baseline、business schema 3 与 knowledge schema 2，拒绝旧库，不复制或兼容旧 tasks 表。旧表仍纳入非空库检测，避免被误认为可新建的空库。测试使用新的隔离运行槽位，保留旧环境。

开发与生产 Compose 默认执行预算为二十四小时（86400 秒）；Python 未配置时的六小时兜底仅适用于未通过 Compose 装配的直接调用。显式覆盖仍由配置正数/有限性校验限制。保留状态 pending/running/success/failed/cancelled、管理员授权、结果摘要、合作式取消与业务失败判定。AgentRun 的模型和执行入口保持独立，Worker 共享进程与 ARQ 消费机制。 普通用户从文件状态和评估运行查看业务结果，提交提示不引导其访问管理员页面。前端回执仅携带 job_id 并读取权威详情，不构造未消费的参数或乐观快照。评估结果查询要求所属知识库的 EvaluationRun 存在；作业 ID 与评估运行 ID 格式不同，旧作业兜底没有当前 consumer，因此删除该路径。

## 替代方案

- 只调整文案：成本低，但代码、wire 和数据库继续暗示全局 Task，不能闭合目标。
- 全链路采用 BackgroundJob 与 JobTracker：采用，提交、观察与执行关系直接可见，代价是 schema/API/内部入口不兼容。
- 合并所有执行到统一 Task：需要重建 AgentRun 状态和恢复模型，不符合当前范围。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 命名与 API 一致，无旧兼容执行入口 | 旧 tasks 路由或 ARQ 名称仍可用 | 作业提交、HTTP、WorkerSettings | 搜索、unit、真实 HTTP | 旧路由和非法进度上报被拒绝 | 通过 |
| 作业持久化采用新 schema | 旧表被当空库，旧版本接流量 | ORM、schema-init、运行版本检查 | 真实 PostgreSQL schema 与 repository integration | 旧 tasks 表和旧域版本拒绝启动 | 通过 |
| 提交端与执行端分离 | API 导入执行器或投递未提交作业 | dispatch、worker、领域 service | 独立 Review、unit、真实 worker 路径 | 回滚事务无可执行作业，首次发布失败保留 pending | 通过 |
| 后台作业 UI 保留观察与取消语义 | 100% 被当成功、取消回执被当终态 | JobTracker、frontend | 真实 HTTP/PG、frontend gate 与浏览器 | 迟到更新不能覆盖终态 | 通过 |

实际验证使用独立 Compose 项目 `yuxi-background-jobs` 与 fresh 数据目录，复用已有镜像并挂载当前源码；未删除其他环境的数据。Compose 命令使用本地镜像/网络覆盖文件。镜像的 PyMilvus 3.0.1 与仓库固定的 3.0.2 不符，检索 E2E 的首次执行因此失败；仅在隔离 API/worker 容器内安装仓库固定版本后重新验证。

| 实际执行命令 | 结果 |
| --- | --- |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow" -q -p no:cacheprovider` | 2537 passed、55 skipped。`--no-sync` 避免已有镜像 uv 修改只读源码的 egg-info；新增 5 个无效默认超时配置负向案例。 |
| 同一 pytest 入口运行 `test/integration/services/test_background_job_repository.py test/integration/services/test_schema_migration_version.py` | 31 passed，真实 PG 回读新表、约束、owner 字段及精确版本；旧任务表、旧版本和迟到写入负控均成立。 |
| 同一入口运行 `test/integration/api/test_background_jobs_router.py test/integration/api/test_dataset_generation_resume_router.py` | 9 passed，真实 HTTP 验证管理员授权、旧路由 404、无公共上报、取消协议、安全摘要及数据集恢复去重。 |
| 停止 API/worker 后以 `docker compose run --rm --no-deps api` 运行 `test_prepare_job_with_failed_initial_arq_publication`，随后启动 API/worker 运行 `test_shipping_worker_startup_recovers_pending_publication test_shipping_worker_failure_runs_domain_hook` | 1 + 2 passed，使用同一 `BACKGROUND_JOB_GATE_ID=naming_final`；回读 PG 作业与领域结果证明真实 Redis/ARQ 执行、补发和失败收敛。 |
| 同一入口运行 `test/integration/services/test_knowledge_projection_integrity.py test/integration/services/test_database_relation_integrity.py test/integration/services/test_database_access_paths.py` | 19 + 2 passed，真实 PG 代次、删除、owner 保护与新表查询计划。 |
| 同一入口运行 `test/e2e/test_document_parsing_artifacts_e2e.py::test_knowledge_worker_hosts_document_resources_and_file_delete_reclaims_objects` | 1 passed，真实 Worker 解析、MinIO/PG 产物、失权上传回收、重建及异步删除后直接读取对象，其他文件负控保留。 |
| 同一 pytest 入口运行 `test/e2e/test_milvus_text_retrieval_e2e.py` | 1 passed，固定 embedding replay 配合真实 Worker/PG/Milvus，验证新回执、索引、三种检索模式、正文约束、重建与删除。 |
| `docker compose exec -T frontend npm run lint`、`npm run test:unit`、`npm run build` | 全部通过，446 unit passed；保留现有大 chunk 提示。 |

fresh Python 导入 `knowledge.evaluation.service` 与 `background_jobs.dispatch` 后检查 `sys.modules`，确认未加载 worker 执行模块。全部 integration collection 443 项成功。Ruff 后端源码与变更 Python 格式检查通过。

浏览器通过真实管理员登录，读取新 HTTP 与 PG fixture：部分失败保持业务上报的 success，摘要不泄漏原始错误；100% running 保持“进行中”；取消受理保持 running，执行方确认并回读 PG 后显示“已取消”。本地截图记录实际 DOM。

`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts scripts.test_release_workflows`、`pnpm --dir docs build` 与 `git diff --check` 全部通过，脚本单元测试 74 passed，文档包含相对链接检查。独立 Reviewer 检查完整需求、diff、测试和规范，修复 API 类型依赖、配置启动校验及 E2E/查询计划旧名称遗漏。

补充归属验证：`test/unit/knowledge/eval/test_result_filters.py test/integration/api/test_evaluation_run_visibility_router.py` 8 passed；人为建立同名后台作业时，缺失/跨库评估运行仍由 HTTP 返回 404。修复前两项 unit 因旧兜底的字典属性访问失败；修复后查询始终依据领域运行归属。历史执行命令保留原文，命名后证据单独记录。

开发与生产 Compose 默认执行预算为 86400 秒。开发拓扑解析后 API 与 worker 均收到该预算；ARQ worker 超时在此基础上增加 30 秒。生产 .env 未提供，生产拓扑解析与部署未验证。

未验证：完整外部 LLM 评估、完整图谱构建、Worker 强制 kill 的额外 E2E 和远端 CI。

旧能力不存在：检查 shipping 源码、模型装配、配置、wire、worker 注册、测试、CI 与当前文档；删除 Tasker/旧公共 Task 类型/旧目录、旧表创建和旧任务路由，不保留别名。

重新引入条件：明确需求与真实 consumer 支持全局任务概念，且提供 AgentRun 和后台作业各自的语义 Owner 与验证后，才考虑统一展示或执行模型。

## 后果

这是 Schema、业务资源关联和 API 的不兼容变更；已有部署不能直接切到新代码。数据库版本明确拒绝旧 schema。本地验证不得删除或修改其他槽位的数据，完整外部模型评估与图谱链路按环境记录未验证范围。
