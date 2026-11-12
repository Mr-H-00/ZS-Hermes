# 协作中的 Agent 发现与选择

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/services/cooperation.py

## 问题

模型能创建独立协作会话并查询整树成员，却不能发现当前用户可调用的 Agent 配置或将工作交给另一份配置。用户需要在同一协作树中发现并选择已有 Agent，沿用当前会话的工作目录、执行归属和权限约束。

目标是提供 Agent 目录工具和按 Agent 创建子会话；非目标是新建 Agent 目录存储、专用子 Agent 后端、跨用户协作或修改执行容量、队列与等待机制。

## 决策

Agent 发现与配置选择补充[统一 Session 协作模型](./2026-10-03-session-cooperation.md)，其会话身份、输入队列、共享环境与等待机制继续沿用。

### 实现方案

`CooperationMiddleware` 注册无参数 `list_agents`，协作服务从持久 Run 的 UID 读取当前有效用户，复用 `list_public_agents` 的可调用范围，仅返回 Agent 的稳定 slug、名称和描述。Agent repository 继续拥有私有、共享和 APP 终端用户的最终可见性。

`create_session` 增加可选 `agent_id`。省略时继承派发方实际模型和配置；指定时按当前执行用户重新校验目标 Agent 的运行权限，冻结目标配置并沿现有 Input/Receipt、Turn/Run 和 worker 链路执行。模型选择使用目标 Agent 的配置及已有系统默认解析，审批模式保持派发方的运行选择。每个子会话继续共享原树、用户、APP、Project 与沙盒，独立保存历史。

创建请求的稳定身份继续由来源 Turn 和工具调用 ID 决定；重放不得更换目标 Agent。目录不包含配置正文或管理权限。运行前以及模型、工具边界沿用既有授权复查。

## 替代方案

- 仅将 HTTP 目录注册为工具：满足发现，但不能选择目标配置。
- 把 Agent 列表固定注入 prompt：增加模型输入并让目录权限和新增配置滞后。
- 单独维护可协作 Agent 白名单：复制现有 Agent 目录和授权事实，增加第二套维护入口。

## 后果

目录权限随账号和共享范围变化，创建仍需即时校验。目标配置在创建时冻结；空配置快照在执行与主动压缩时保留默认值，不重新读取后来修改的配置。后续执行依赖仍按当前用户重新授权。

审批模式保持派发方当前运行选择；所选模型的配置错误沿用既有输入解析失败，不静默替换。无需 Schema 变更。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 模型能列出当前执行用户可调用的 Agent | 目录暴露不可运行的私有或共享配置 | directory service、AgentRepository | 真实 PostgreSQL 目录与 HTTP 权限测试、工具 Schema | 其他用户私有 Agent、无授权共享 Agent、APP 终端用户 | Passed |
| 指定 Agent 后执行其配置，默认创建保留继承 | 只改变名称或 Agent ID，执行仍使用父配置 | cooperation service、preparation | PostgreSQL 回读 Session/Input/Run；确定性 worker E2E 回读实际模型 manifest 和文件 | 父与目标使用不同提示词和工具；空目标创建后修改配置 | Passed |
| 选择与重放在副作用前受权限和幂等约束 | 不可见目标产生子会话，重放更换配置 | cooperation service、AgentRepository | PostgreSQL 回读成员、输入和回执数量 | 不可见/不存在目标、失效用户、同工具调用换 Agent | Passed |
| Shipping 构图包含发现和选择工具 | helper 已实现但模型不可调用 | CooperationMiddleware、ChatbotAgent | Schema unit、确定性 E2E | 重放模型拒绝缺失工具或错误目标提示词 | Passed |

临时恢复准备阶段的 `snapshot or live_config` 后，PostgreSQL 空快照案例因读到 `CHANGED_AFTER_CREATION` 失败；恢复压缩阶段同一缺陷后，两项空快照 unit 因读到 `CHANGED_SUMMARY` 失败；恢复创建时的 null 快照缺陷后，对应 PostgreSQL 用例因保存 `None` 而非 `{}` 失败。修正版本均通过。CI 的持久化 gate 显式传入已有管理员凭据，治理权限负控不会因缺少登录前置而跳过。

`pytest test/integration/services/test_session_cooperation.py -q --tb=short -p no:cacheprovider` 为 55 passed；真实 HTTP 的 `test/integration/api/test_permission_convergence.py::test_private_agent_crud_isolation_governance_and_app_default` 通过，验证管理权限与运行权限分开，以及 APP 私有配置拒绝。

`pytest test/e2e/test_session_cooperation_e2e.py -q --tb=short -p no:cacheprovider` 收集 5 项并在容器内退出 0；宿主终端连接中断，没有完整 pytest 摘要。补充实际模型 manifest 断言后的 `test_sessions_use_public_state_and_shared_sandbox[False-True]` 单独复跑为 1 passed，日志在容器内持久捕获。该场景从目录返回值选择 Agent，回读成员、Run、模型 manifest 与共享文件，并验证压缩和沙盒重建。

后端全量 `pytest test/unit -m "not slow" -q -p no:cacheprovider` 为 2556 passed、55 skipped；工程信任检查和其 64 项 unit 通过，相关 Python 文件的 Ruff check 与 format check 通过。容器内使用 `uv run --no-sync --group test`：普通同步命令在现有镜像的 root 所有 egg-info 时间戳处遇到权限错误，未把该命令记为通过。

文档 `npm run build` 通过；`pnpm run build` 因共享 `node_modules` 符号链接被拒绝，实际构建执行同一 VitePress 脚本。工程信任检查与 `git diff --check` 通过。

真实外部模型自主选择与协作质量未验证。确定性场景使用真实 PostgreSQL、HTTP、worker 和沙盒；图谱与 Milvus 外部实例未启动，本功能没有调用这些外部能力。
