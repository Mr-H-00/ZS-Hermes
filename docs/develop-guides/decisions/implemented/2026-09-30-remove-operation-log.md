# 删除身份操作日志机制

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/identity/services/administration.py

## 问题

身份操作日志只写入 PostgreSQL，没有查询 API、展示或业务消费。用户决定完全移除该机制，身份管理不再承担操作日志写入及其失败依赖。

## 决策

### 实现方案

删除 system 域的操作日志函数、ORM 模型及 User 关联，移除身份路由、OIDC 和身份管理服务的写入点及仅供日志使用的参数和变量。身份管理服务继续拥有部门和管理员的原子事务；登录失败计数、账号锁定、权限检查和业务提交保持原有语义。模型加载和新库 Schema 初始化不再创建日志表。

Schema 入口只初始化新库，已有部署不自动执行破坏性 DDL。历史 `operation_logs` 表不再被应用访问，运维可在备份后执行 `DROP TABLE IF EXISTS operation_logs` 清理；删除历史记录不可逆。本任务不删除正在运行的数据库中的历史数据。

## 替代方案

保留写入并增加查询页面需要新的产品需求和维护成本；保留模型但停写会留下无消费者的代码与 Schema 表面。两者均不采用。

## 验证

用户明确以静态检查作为最终验收范围，不再补跑运行测试；以下保留已经执行的命令及环境限制。

- Passed：相关源码 Ruff 检查与格式检查、`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（64 个测试）、`cd docs && pnpm run build`、`git diff --check`。
- Passed：`docker compose exec api uv run --no-sync --group test pytest test/unit/services/test_oidc_service.py test/integration/services/test_identity_admin_service.py test/integration/api/test_auth_router.py -q` 中的 6 个 OIDC unit 案例。
- Passed：通过 `docker compose exec api uv run --no-sync --group test python` 加载全部 ORM 后直接执行 identity service 测试函数，真实 PostgreSQL 回读证明用户冲突回滚、并发初始化只有一套部门与用户、新 Schema 不存在日志表。该执行不使用集成测试公共 fixture。
- Not run：上述 pytest 命令的 15 个 integration 案例在公共 fixture 阶段失败，数据库缺少 `agent_input_receipts` 表，HTTP 行为尚未验证。
- Not run：`docker compose exec api uv run --group test pytest test/unit -m "not slow" -q` 在 editable package 构建时因 `yuxi.egg-info` 权限失败；加 `--no-sync` 的全量 unit 执行被并行环境重建中断，退出码 137，不能认定全量通过。
- Inspected：独立 Reviewer 核对完整需求、本变更 diff、规范及上述测试限制；源码中无日志模型、关系、写入函数和专属参数。

旧能力不存在：日志函数、模型、关系、写入点及专属测试移除，新库不存在日志表。

重新引入条件：有明确审计消费者、保留策略和授权边界的新需求，单独决定持久化及查询契约。

## 后果

不再保留身份操作追溯记录；现有历史表需要部署方独立清理。Agent 的 Model/Tool 审计及普通运行日志属于其他机制，不在删除范围。
