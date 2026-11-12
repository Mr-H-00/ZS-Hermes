# 后端启动、基础设施与 Worker 模块的局部阅读顺序

状态：implemented
类型：simplification
Owner：backend/yuxi/bootstrap/api.py

## 问题

Bootstrap、Infrastructure 和 Workers 中的主生命周期、资源管理与私有 helper 在部分文件中交错。读者需要先跳过实现细节，才能确认启动顺序、required/optional 语义以及创建和关闭资源的关系。整理不能改变注册顺序、惰性加载、事务提交点或资源释放语义。

## 决策

采用按职责的局部阅读顺序，而不是全目录机械重排：

- `bootstrap/api.py` 先展示 API 启动主流程，再展示关闭适配和启动组件 helper。
- `bootstrap/worker.py` 先展示 worker 启动与关闭流程，再展示 reconciliation 和健康上报循环；取消、Schema 校验、数据库提交和队列关闭顺序保持不变。
- `infrastructure/redis/manager.py` 按配置、同步/异步创建、获取/关闭、ARQ 入口、私有转换与关闭 helper 排列，创建和关闭接口保持同一模块内可连续查找。
- `shared` 小模块补充职责说明；公共时间、文件、哈希和 Singleton 契约不新增跨域抽象。
- `workers/settings.py` 补充配置职责和 `WorkerSettings` 说明；`infrastructure/observability/logging.py` 明确 import-time 日志初始化职责，不移动其初始化顺序。

本次只整理上述明确阅读障碍，没有移动文档解析、provider/task 注册或其他存在 import-time/惰性加载约束的模块。

### 实现方案

生命周期入口保留现有框架回调名称。`_startup`、`_worker_startup` 和对应 shutdown 流程在文件中先出现，内部启动组件、reconciliation 和健康 helper 位于主流程之后。Redis 的公共创建/获取/关闭接口在私有环境转换、客户端关闭和初始化锁 helper 之前出现；Python 运行时查找保证其对后置 helper 的调用语义不变。所有代码变化均为模块内等价移动或职责 docstring。

## 替代方案

- 全目录按函数名或下划线机械重排：不采用，会遮蔽注册、定义时依赖和生命周期顺序。
- 立即拆分为多个新模块：不采用，当前目标是降低阅读跳转，新增 import/export 表面没有验收价值。
- 不整理，只靠文档说明：不采用，主流程顺序属于源码 Owner，读者仍需跨 helper 跳转。

## 后果

启动和关闭顺序可以在 Bootstrap 文件中连续阅读，Redis 的资源生命周期也更容易成对核对。未引入新的公共 API、配置、依赖或兼容层。部分私有函数依赖后置定义，这是 Python 模块加载完成后才执行调用的既有安全语义；若未来出现 import-time 调用，应恢复定义时依赖优先，而不是继续追求视觉顺序。

## 验证

- `python3 -m compileall -q backend/yuxi/bootstrap backend/yuxi/infrastructure/redis backend/yuxi/shared`：Passed。
- `docker compose exec -T api uv run ruff check yuxi/bootstrap/api.py yuxi/bootstrap/worker.py yuxi/bootstrap/task_handlers.py yuxi/infrastructure/observability/logging.py yuxi/infrastructure/postgres/manager.py yuxi/infrastructure/redis/manager.py yuxi/shared/hashing.py yuxi/shared/singleton.py yuxi/workers/settings.py`：Passed。
- `docker compose exec -T api uv run ruff format --check yuxi/bootstrap/api.py yuxi/bootstrap/worker.py yuxi/bootstrap/task_handlers.py yuxi/infrastructure/observability/logging.py yuxi/infrastructure/postgres/manager.py yuxi/infrastructure/redis/manager.py yuxi/shared/hashing.py yuxi/shared/singleton.py yuxi/workers/settings.py`：Passed。
- `docker compose exec -T api uv run --group test pytest test/unit/utils/test_lifespan.py test/unit/services/test_run_worker.py test/unit/storage/test_redis_manager.py -q`：75 passed。
- 生命周期和公共接口符号顺序脚本：Passed；确认 Bootstrap 生命周期先于内部 helper，Redis 公共接口先于私有 helper。
- `git diff --check`：Passed。
- 旧能力不存在：Passed；未新增第二套启动入口、Redis client manager 或 shared 公共契约，原有入口继续是唯一调用表面。
- 重新引入条件：若真实消费者需要独立模块职责、现有文件承担多个外部适配主题，或运行时证据证明当前布局阻碍修复，则另行提出拆分决策。
- `docker compose exec -T api uv run --group test pytest test/unit -m "not slow"`：2427 passed, 55 skipped；本次变更未改运行语义，真实 Compose worker 关闭/重启链路仍需 integration/E2E 门禁确认。
- `docker compose exec -T api uv run ruff check yuxi` 与 `docker compose exec -T api uv run ruff format yuxi --check`：Not passed；仓库中未由本次整理修改的 `yuxi/api/routers/agents/management.py` 存在既有 E501/format 问题，受影响文件的 ruff 检查已单独通过。
- 未执行 integration、E2E；真实 Compose worker 关闭/重启链路仍未验证。
