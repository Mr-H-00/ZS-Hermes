# Agent 默认执行步数上限

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/context.py

## 问题

未显式配置执行步数的智能体在 300 个 LangGraph 步骤后失败，复杂任务需要允许执行到 2000 步。

## 决策

### 实现方案

`BaseContext.max_execution_steps` 的默认值设为 2000，配置 Schema 同步生成默认值与说明。`BaseAgent` 沿用现有 Context 到 LangGraph `config.recursion_limit` 的传递路径；管理员保存的显式配置继续优先。会话创建时固化配置快照，已有会话的后续输入与 Run 保持原值。配置参考说明管理员调整方式与新建会话的生效范围。

## 替代方案

只把单个智能体的持久配置设为 2000 可以解决该智能体的后续运行，但其余使用默认配置的智能体仍受 300 步限制。保留默认上限更低的资源预算；统一提高默认值满足本次明确要求。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 默认图执行配置的上限是 2000 | Context 或图入口仍使用 300 | context.py / base.py | BaseAgent 配置 unit | 保持旧常量时断言实际 300 与预期 2000 不符 | Passed |
| 显式配置继续覆盖默认值 | 图入口硬编码默认值 | base.py | 显式 42 与 2000 的参数化 unit | 硬编码 2000 时显式 42 案例失败 | Passed |

`docker compose exec -T api uv run --no-sync --group test pytest test/unit/services/test_base_agent_langfuse_config.py test/unit/agents/test_context_auth.py -q`：29 passed。默认值断言在旧实现上得到 300 并失败。标准 `uv run --group test` 命令因容器中 `yuxi.egg-info` 权限错误无法构建 editable 包，替代命令使用已安装依赖。完整模型任务未验证；本次只改变现有配置默认值，不改变 Run 状态或派发链路。

在 API 容器使用已安装的真实 LangGraph 构造 400 步后结束的图：`recursion_limit=300` 触发 `GraphRecursionError`，读取 `BaseContext` 默认 2000 后执行得到最终 `steps=400`。Worker 容器的新 Python 进程回读默认值为 2000。这些探针验证框架配置与运行环境，不替代完整模型任务的 E2E。

## 后果

长任务最多允许更多模型与工具调用，时间与费用预算随任务增长。提高步数上限不修复循环逻辑。已保存为 300 的智能体需要管理员显式改为 2000；已有会话需要新建会话才能使用新上限。本变更不修改持久配置或重试已失败的运行。
