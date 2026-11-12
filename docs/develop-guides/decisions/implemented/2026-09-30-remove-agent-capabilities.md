# 删除 Agent 静态能力列表

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/base.py

## 问题

Agent 的静态字符串列表重复声明平台提供的上传、文件和主动上下文压缩功能，前后端额外维护开关链。当前主智能体后端统一为 Chatbot，子智能体由专用入口和 repository 的 kind 过滤区分。

## 决策

### 实现方案

删除 BaseAgent、内置后端和 Agent 序列化中的 capabilities。上传与文件展示作为聊天平台功能固定提供；前端删除上传开关传递、文件状态能力判断和压缩入口能力判断。压缩 service 保留主智能体可见性、Thread 作用域、行锁与空闲检查，只删除字符串能力检查。系统 discovery、模型供应商与 parser 能力声明保持各自职责。

## 替代方案

保留列表或改成布尔字段仍要求维护重复声明；以 backend_id 驱动前端也会把后端装配细节传入聊天流程。当前消费者无需这些替代机制。

## 验证

旧能力不存在：Agent 类、响应和聊天前端无静态能力声明或消费，上传组件无对应开关 prop。

重新引入条件：出现真实的主智能体后端功能差异，并由实现与契约测试证明平台接口无法统一提供该功能。

后端针对性验证：`docker compose exec -T api uv run --no-sync --group test pytest test/unit/repositories/test_agent_repository.py test/unit/agents/test_builtin_discovery.py test/unit/services/test_context_compression_service.py -q`，74 passed。真实主子后端的发现与角色序列化响应断言字段不存在；无能力属性的压缩测试继续验证结果，跨 APP 与繁忙线程的负向案例保留。

前端验证：`docker compose exec -T frontend pnpm run lint:check` 通过；`docker compose exec -T frontend pnpm run test:unit`，384 passed。SSE 状态测试直接省略能力参数，仍回读状态与请求版本。

工程与文档验证：`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（64 tests）、`cd docs && pnpm run build` 与 `git diff --check` 通过。

完整证据受环境与工作树其余改动限制：标准 `uv run --group test` 在容器内因 `/app/package/yuxi.egg-info` 不可写而构建失败，针对性测试使用现有环境的 `--no-sync`。`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"` 结果为 2189 passed、29 failed、27 errors、55 skipped，未达到全量通过。`docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_builtin_discovery.py test/integration/api/test_context_compression_router.py -q` 的四个案例均被 fixture 缺失 `agent_input_receipts` 表阻塞。`docker compose exec -T frontend pnpm run build` 被 KnowledgeGraphSection 对已删除 graph_api 的导入阻塞。

真实浏览器登录聊天页并回读 DOM，上传菜单存在；API 500 使页面未就绪，完整上传、文件产物与压缩的端到端结果未验证。通过 playwright-cli 在同一浏览器独立挂载实际 AgentInputArea SFC，省略能力参数时附件和图片菜单 enabled，拖拽文本文件发出 upload-attachment 事件，disabled 后两项菜单均禁用；独立组件验证不替代后端持久化与端到端证据。

## 后果

外部消费者需停止读取 Agent 响应的 capabilities 字段；本决定不增加兼容字段，无 Schema 迁移。
