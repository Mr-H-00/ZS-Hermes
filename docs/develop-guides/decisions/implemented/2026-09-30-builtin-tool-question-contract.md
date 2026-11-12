# 内置工具拆分与提问入口契约

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/extensions/tools/builtin/ask_user_question.py

## 问题

内置工具的大文件混合搜索、OCR、交付物和提问职责。提问参数在工具、执行服务和前端重复规范化，支持多种包装、别名和类型转换，非法输入可以被过滤或投影成占位问题，增加协议歧义。

## 决策

### 实现方案

按工具职责拆分 Python 模块，由 builtin 包显式导入完成注册；OCR 与交付物实际共用的 runtime scope 查询放在同目录的共用模块，不保留 tools.py 转发层。搜索供应商选择与惰性 parser 加载保持原行为。

提问模块同时拥有嵌套 Pydantic 参数 schema 和工具实现。模型输入只接受标准 questions 列表及 question、options、multi_select、allow_other、question_id 和 operation 字段；选项只接受 label/value 对象。入口校验非空文本、布尔类型和数量，补默认字段与确定性 question_id，拒绝重复问题 ID。LangGraph interrupt 保存标准结构，执行服务只投影已保存的问题，前端只映射 snake_case 和推导展示类型。回答和恢复绑定原问题 ID，保持 Turn/Run 和 resume 协议。

部署收窄 schema 前，操作者停止新提问，并完成或取消已有提问等待点，再替换 API 和 worker。checkpoint 同时保留原始模型工具参数；LangGraph 恢复会重执行工具节点，旧入口接受的字符串选项、字符串布尔值等原始参数不满足新 schema，标准化等待点本身不能提供升级兼容。不自动迁移或取消持久数据，不保留运行时宽松解析。标准原始调用参数的 checkpoint 可以直接恢复。

审批策略位置、沙盒授权行为、搜索 provider 行为和提问弹窗视觉布局不属于该决定。纯问答与回答格式沿用[交互决策](2026-09-18-question-dialog-free-text.md)。参数示例和升级前置条件由[工具系统](../../../agents/tools-system.md)维护。

## 替代方案

- 保持原文件和重复解析：职责与协议歧义继续存在。
- 只搬迁文件：能改善导航，不能闭合入口契约。
- 全部移到前端解析：CLI/API 无法共享标准等待点，问题 ID 可能漂移。
- 永久保留格式兼容层：继续维护没有标准契约约束的输入，按部署排空前置条件退出。
- 自动迁移原始 checkpoint 参数：需要识别完整执行图、多工具调用和审计归属；当前拆分不引入数据改写机制，选择显式部署排空。

## 后果

工具模型可见 schema 收窄，依赖 JSON 字符串、字段别名或字符串选项的调用会收到参数错误，不创建人工等待。非空但部分非法的问题列表整体拒绝，不静默过滤。Web 在工具完成前可以展示标准字段的原始参数，完成后以工具结果中的标准问题为准，不猜测问题 ID。

旧的非标准原始调用参数没有跨 schema 升级恢复承诺，部署前排空是显式前置条件；操作者未满足条件时，旧回答可能无法进入工具结果。代码不以标准化等待点替代原始 checkpoint 的事实。真实 provider 漂移与确定性 E2E 分开报告。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest -p no:cacheprovider test/unit/agents/toolkits/buildin/test_ask_user_question.py -q`：28 passed。覆盖模型 schema、入口标准结构、非法参数整体拒绝、重复 ID、注册唯一性及真实 LangGraph 旧工具 checkpoint → 新工具重放；标准调用恢复原答案，非标准调用明确产生工具错误，证明部署排空前置条件的原因。
- 模块拆分但行为不变时的工具、OCR、交付物与中断最小集合：47 passed；收窄契约后的相关集合：55 passed；`test/unit/middlewares/test_model_input_middleware.py`：9 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest -p no:cacheprovider test/e2e/test_agent_lifecycle_e2e.py -k 'invalid_question_parameters or waiting_turn_requires_complete_answers or cancel_waiting_turn' -q`：提问恢复和非法参数两项通过，额外取消等待回归失败于“模型未进入阻塞位置”；独占 replay 和唯一 provider 的隔离运行仍有相同失败，不能写作取消链路通过。失败测试资源通过公开取消接口清理，并回读数据库为 cancelled；取消失败的归因未闭合。
- `docker compose exec -T api uv run --group test pytest test/unit -m 'not slow'`：依赖同步因镜像 /app/yuxi.egg-info 目录权限失败；使用现有依赖的 `--no-sync` 完整 unit 最终回归：2411 passed、55 skipped、7 subtests passed，包含 checkpoint 重放案例。
- `docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`：通过，unit 385 passed，build 保留 chunk size 提示。相关问题/弹窗 unit：5 passed，验证等待点与 SSE 原问题结构进入组件，回答 ID 不变。
- Playwright 使用真实 Vue 组件及标准等待点，在 1440×900 浅色和 375×812 暗色检查文本、选择和工具结果优先展示；读取 DOM 中的提交结果为原 question_id 对应的答案。截图保存在本地临时路径，不提交运行产物。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、`cd docs && pnpm run build` 和 `git diff --check`：通过，verifier unit 64 passed。前端最终 lint 和相关 unit 再次通过。真实 provider 与生产升级排空没有执行，不将确定性测试视为此类验证。

旧能力不存在：源码与测试搜索确认 runtime/questions.py、builtin/tools.py、前端包装与布尔兼容解析，以及服务层问题规范化和占位题生成均不再存在。

重新引入条件：存在可重现的外部契约或无法排空的持久调用消费者，并明确兼容边界、退出条件和回归证据；模型偶发不规范参数由工具错误反馈处理。
