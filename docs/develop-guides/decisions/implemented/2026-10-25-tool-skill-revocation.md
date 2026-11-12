# 工具执行前刷新 Skill 授权快照

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/middlewares/authorization.py

## 问题

候选 `943c73dc` 的真实 worker E2E 在 Skill 撤权后仍成功执行 `present_artifacts`，违反执行前按当前授权筛选工具的机制契约。现有分级刷新仅在模型边界更新 Skill 快照，工具边界保留旧依赖授权；身份和 MCP 复查不足以拒绝被撤销 Skill 引入的本地工具。

## 决策

### 实现方案

`awrap_tool_call` 复用 `_refresh_execution_resources`，在判断 `tool_allowed` 前复查身份、当前资源选择、Skill 快照与 MCP 策略。工具边界不调用 `sync_agent_context_skills`，保留文件投影只在实际模型或摘要请求前同步的优化。模型与摘要入口不变，不引入缓存、版本戳或新的授权模型。

本决定部分取代[分级刷新](2026-10-24-execution-authorization-refresh-scope.md)的工具边界快照滞后规则；身份复查、MCP 策略和文件投影分级保留。机制文档同步明确资源快照与文件投影的刷新边界。嵌套 MCP 装配 integration 使用当前注册的 `ocr_parse_file` 作为依赖工具，替换已移除的本地 `list_kbs` 夹具，继续验证连接开关与未激活 MCP 不被发现的行为。

## 替代方案

- 放宽 E2E 允许撤权后继续执行：不能满足当前工具执行契约。
- 工具前恢复全部文件投影同步：恢复额外文件遍历和锁开销，拒绝本地依赖工具只需要当前资源快照。
- 增加授权缓存或版本机制：扩大维护表面，没有当前必要性。

## 后果

每工具调用增加资源快照查询与必要 Skill 读取，不声称性能无影响。审批恢复后的 Skill 文件投影仍按模型边界同步，此变更恢复依赖工具的即时授权；不恢复每工具调用重建文件投影。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 撤销 Skill 后下一工具调用拒绝，聊天与历史保留 | 旧快照继续授予本地工具 | authorization、Skill 可见性查询、既有撤权 E2E | 真实 HTTP、worker、checkpoint 工具结果与模型协议回读 | `943c73dc` 的同一 E2E 确实成功执行并失败 | Passed，本地 11 项 E2E |
| 工具边界仍不重建文件投影 | 将授权修复扩大为文件副作用 | authorization、sync_agent_context_skills | 授权 unit 与 PostgreSQL integration | 工具刷新快照但投影同步断言保持为零；既有 E2E 在旧入口失败 | Passed，本地 25 项 |

实际执行：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/middlewares/test_runtime_authorization.py test/integration/services/test_runtime_authorization_boundaries.py -q --tb=short`：Passed，25 项。初跑 24 passed / 1 failed，失败来自已移除本地知识工具的夹具；替换为当前注册工具后完整集合通过。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_permission_revocation_e2e.py -q --tb=short`：Passed，11 项，116.83 秒。回读真实 checkpoint 的拒绝 ToolMessage、模型协议及 Run/文件事实，未修改既有 E2E oracle。
- Ruff 常规检查、import 检查与 format check：Passed，441 个源码文件。

原候选[系统检查](https://github.com/xerrors/Yuxi/actions/runs/40173931079)在相同 E2E 失败，持久 ToolMessage 为成功结果，是本修复的直接负向证据。新提交的远端完整 CI 在提交时尚未运行，不用旧镜像或本地结果替代。
