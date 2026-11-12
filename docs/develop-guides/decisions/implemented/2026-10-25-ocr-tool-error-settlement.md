# OCR 临时文件与工具异常终态

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/middlewares/tool_error_guard.py

## 问题

OCR 调用方未创建下载目标，而 Workspace 下载只打开既有文件，导致解析前失败。工具生命周期来自内层 BaseTool 回调，工具最终结果由外层中间件决定：捕获异常、直接返回权限错误或改写结果时，模型收到的 ToolMessage 与公开事件可能不一致，公开调用和结果可能持续处于 in_progress。

## 决策

### 实现方案

OCR 工具在自身临时目录预先创建目标文件，保持 Workspace 的授权、有界复制和 no-follow 契约。ToolErrorGuardMiddleware 用调用局部的回调副本观察真实 BaseTool 开始和原始输出，再发布最终结局；最终 ToolMessage 决定失败、错误正文与恢复成功；仅当原始与最终结果都成功时，复用压缩前的原文。Command 按本次 tool_call_id 提取结果，与记录器共用提取逻辑，原 Command 继续返回执行图。捕获异常时发布同一错误 ToolMessage。模型继续接收中间件处理后的返回值。控制异常继续传播，真实工具已开始时记录错误观察；取消观察在原有执行清理完成后发布。

BaseAgent 保留原生开始与 output-delta，只将内层结束、错误回调替换为 Guard 的内部 custom 结局。真实执行复用 tool-finished；中间件直接返回的结果使用内部 tool-result，由 repository 从当前 Run 的模型声明建立无 started_at 的结果记录，不伪造工具执行，不计入执行统计。记录器不增加待确认错误集合或历史扫描。最终 checkpoint 对账同步保存公开结果，提交后由现有结束事件发布。

这补全[工具执行事实收敛](2026-10-15-tool-execution-convergence.md)中的结果入口，持久事实仍由 tool_audit Message 拥有，不改变公开协议或 Schema。

## 替代方案

- 直接把全部 tool-error 判为失败会把人工中断误判为错误，事件只有 message 字符串，无法可靠区分类型。
- 改为文件流解析或扩大公共下载接口的创建行为会改变更多消费者的契约。
- 前端根据报错文本停止转圈会留下持久公开状态不一致。
- 在记录器追踪待确认异常并扫描 values 会重复推断执行结局；只在 Guard 的 except 补结束事件则漏掉中间件直接返回错误的路径。
- 直接用最外层返回替换原始审计会丢失大结果原文；统一伪造开始事件会把权限拒绝计为工具已执行。调用局部回调只保留既有事实，不建立额外持久副本。无条件优先原始输出会覆盖中间件的最终失败或恢复结果，因此原文复用仅适用于成功结果。

## 后果

回调配置必须复制后附加观察器，不能修改并发调用共享的 manager。观察器只接受自身首次开始的执行身份，嵌套调用不能覆盖外层原文。取消、权限撤销和人工中断不转换为普通错误 ToolMessage。失败正文以最终 ToolMessage 为准；若正文已被大结果策略外置，公开结果保留其文件指针，不以早先错误覆盖最终原因。BaseAgent 的工具终态转接要求执行图装配最外层 Guard，当前 Chatbot 构图统一满足此约束。历史已完成会话的修复不在运行时增加兼容分支。

## 验证

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`：3295 passed、122 skipped。容器缺少宿主文件的配置测试另以 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q -p no:cacheprovider` 验证，204 passed。
- 最终实现运行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit/middlewares/test_tool_error_guard.py test/unit/agents/test_base_tool_event_normalize.py test/unit/services/test_run_message_recorder.py test/integration/services/test_tool_result_projection.py -q -p no:cacheprovider`：78 passed。包含完整批次后补充的无效 Command 回归，以及真实 PostgreSQL 的结果归属、公开状态和拒绝后持久事实不变断言。
- API 与 worker 加载修复后，以 `docker compose exec -T -e OPENAI_REPLAY_URL=http://api:8765/v1 api uv run --no-sync --group test pytest test/e2e/test_agent_lifecycle_e2e.py::test_invalid_question_parameters_return_tool_error_without_waitpoint test/e2e/test_agent_lifecycle_e2e.py::test_waiting_turn_requires_complete_answers_and_resumes_same_turn test/e2e/test_agent_lifecycle_e2e.py::test_cancel_waiting_turn_pauses_queue_until_continue test/e2e/test_agent_lifecycle_e2e.py::test_cancel_running_model_closes_audit_without_foreign_output test/e2e/test_agent_lifecycle_e2e.py::test_large_tool_approval_resume_keeps_original_audit -q -p no:cacheprovider` 验证真实 HTTP、PostgreSQL、Redis、worker 和 SSE：6 passed。覆盖错误结果先于后续模型输出发布、人工等待、取消及大结果审批恢复后的原文保留。
- 负向证据：真实图 `post_error`、`post_command_error` 用例在无条件优先原始输出的实现上均以 `success != error` 失败；独立进程恢复无条件解析原始 Command 后，`invalid_command` 用例因二次抛出 `ValueError` 失败。同步与异步用例同时验证最终错误正文、错误恢复及 Command 调用归属，原结果对象不被修改。
- OCR 的真实 DOCX 回归包含在完整单测中，使用真实授权下载、解析与 Workdir，并回读正文。未调用外部 OCR 引擎；完整 E2E 套件与真实模型 provider 探针未执行。
- 工程契约检查与其 66 个单测、后端 Ruff lint/format/import 检查、文档构建及 `git diff --check` 通过。文档构建验证相对链接；独立 Reviewer 核对完整 diff、语义 Owner 与测试边界。
