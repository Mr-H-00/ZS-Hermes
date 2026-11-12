# 等待取消的 checkpoint 清理归属

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/checkpoint_cleanup.py

## 问题

等待取消在业务服务中直接处理 LangGraph 节点、消息配对与 pending writes。重复的消息查找和框架状态提交混在一起，使取消用例难以阅读。

## 决策

### 实现方案

turns 保留 Run、Thread 与工作区归属检查、原图装配和业务终态提交；runtime 提供取消等待 checkpoint 的公开操作。当前批次由最近的 AIMessage 及其之后的 ToolMessage 确定，用同一个纯函数读取。保留原图状态 API 的三阶段提交：END 合入已完成节点结果，补齐缺失的取消结果，END 清空改写触发的任务。任何阶段失败都向调用方传播，业务 Turn 保持 cancelling，重试沿用相同操作。

## 替代方案

- 保留业务服务内实现：行为不变，但业务用例继续拥有框架细节。
- 合并为一次消息改写：代码更短，但会丢失 pending writes 中的并行业务增量。
- 增加持久化清理阶段或等待点批次标识：扩大存储和协议；当前 checkpoint 已包含恢复所需事实。

## 后果

等价重构保留多次 checkpoint 提交和原图 reducer 依赖。取消清理失败仍需现有 cancelling 恢复用例重试，不新增事务、状态或授权路径。

## 验证

| 实际命令 / 检查 | 结果 |
| --- | --- |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit/agents/test_checkpoint_cleanup.py test/unit/agents/test_summary_graph_config.py -q` | Passed：12 项；覆盖并行业务增量保留、重复 call ID、两处提交后失联重试、无工具调用拒绝和维护图原拓扑 |
| `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q` | Passed：2576 passed，55 skipped |
| `docker compose exec -T api uv run --no-sync --group test python -` 的临时 E2E runner | Passed：3 场景；真实 HTTP、PostgreSQL checkpoint、worker、Redis 与沙盒，模型由确定性重放服务提供 |
| 变更文件 Ruff 检查和格式检查 | Passed |
| `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts` | Passed：64 项脚本测试 |
| `cd docs && pnpm run build`、`git diff --check` | Passed |
| 删除旧入口的源码检查 | Inspected：services 只调用 runtime 公开操作，无旧私有清理入口或转发层 |

临时 runner 直接调用现有 E2E 的完整测试函数：`test_sessions_use_public_state_and_shared_sandbox` 的并行问答取消与协作取消参数，以及 `test_cancel_waiting_turn_pauses_queue_until_continue`。沿用函数内的最终状态、原始 checkpoint 和队列继续断言；清理只针对这些函数创建的资源，不加载 pytest 会话中清理全部测试资源的自动 fixture。验证结束后关闭本次启动的两个重放进程。

旧能力不存在：services 的私有 checkpoint 清理实现与旧测试位置均已移除，测试由 runtime 所在的 agents 集合维护。

重新引入条件：出现必须由业务事务拥有的新增清理语义时重新判断职责；具体框架状态操作仍由 runtime 承担。

Not run：整套 E2E、外部真实模型和独立 Agent Review。此侧对话不使用子 Agent，改动未提交。
