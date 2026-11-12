# 执行授权刷新与 Skill 文件投影分级

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/agents/runtime/middlewares/authorization.py

## 问题

授权中间件在每次模型调用前后与每次工具调用都执行完整刷新：打开数据库会话、复查 user/run/agent 可见性、normalize 资源配置、重算 Skill 运行快照、查询 MCP 可用性，并经 `sync_agent_context_skills` 重建用户 Skill 文件投影（pg advisory 锁加线程池文件遍历）。一个含 N 次工具调用的轮次会重复 N+2 次几乎相同的工作；两次模型边界刷新之间没有任何授权事实变化。文件投影重建是其中最重的操作，却按工具调用次数放大，并在并发下按用户串行化。

## 决策

刷新按边界分级，安全判定本身保持每次请求与每次工具调用都重新执行：

- `abefore_model` 复查身份、重建资源与 Skill 快照、解析 MCP 策略，每模型轮次一次。
- `awrap_model_call` 与摘要授权入口 `refresh_execution_authorization` 复查执行身份、解析当前 MCP 策略并同步 Skill 投影；投影同步频率与原先一致（每模型轮次一次），且发生在 Skill 文件被读取的模型轮次开始处。
- `awrap_tool_call` 复查执行身份、刷新资源与 Skill 快照并解析当前 MCP 策略，不重建文件投影。工具边界的快照刷新由[即时 Skill 撤权修正](2026-10-25-tool-skill-revocation.md)拥有，取代将 Skill 授权延迟到下一模型轮次的规则。

`tool_allowed` 使用工具边界刷新后的资源和 Skill 快照、当前激活状态及 MCP 策略；不以构图时的注册集合代替当前授权。

### 实现方案

`authorization.py` 的 `_resolve_mcp_policy` 由两条路径共用：`_refresh_execution_resources` 服务模型输入与工具边界，刷新资源与授权快照；`_execution_mcp_policy` 在实际模型与摘要请求前复查身份和 MCP 策略。`refresh_execution_authorization` 组合后者与 `sync_agent_context_skills`，文件投影按实际模型请求同步。工具执行异常仍把故障 MCP 标记为本 Run 不可用。

## 替代方案

- 每工具调用同步全部文件投影：增加文件遍历，投影 advisory 锁在并发下成为同用户请求的串行点。
- 工具边界完全不查数据库：身份撤销的生效窗口扩大到整轮，违反"模型和工具执行边界重新读取身份与授权"的约定。
- 投影同步增加版本戳或 TTL 缓存：能进一步降低模型边界成本，但需要新的失效通道；当前没有测量证明其必要，留作后续选项。

## 后果

Skill 文件投影在模型边界之间保持不变；审批或协作等待期间被撤销的 Skill 文件，在下一次模型请求同步前仍可能由通用文件工具读取。依赖工具的授权不使用这个文件投影：每工具调用刷新资源与 Skill 快照，MCP 可用性、禁用工具集合和执行身份也在调用前复查。文件投影分级节省文件遍历与同步锁开销，不承诺消除资源快照查询。

## 验证

`test/unit/middlewares/test_runtime_authorization.py` 拥有身份复查、工具资源刷新与不重建文件投影的边界断言，以及网络重试、摘要授权、MCP 故障和警告状态的回归。`test/integration/services/test_runtime_authorization_boundaries.py` 通过真实 PostgreSQL 和 Skill 字节验证模型边界的绑定根冻结、共享正文刷新和撤销/重授收敛；工具边界的真实 worker 撤权证据与实际命令由[即时 Skill 撤权修正](2026-10-25-tool-skill-revocation.md)记录。

旧能力不存在：生产装配中 `awrap_tool_call` 不调用 `refresh_execution_authorization` 与 `sync_agent_context_skills`；候选 MCP 解析由两条授权路径共用，不内联复制。

重新引入条件：若要求 Skill 文件投影在工具批内即时收敛，须以新的明确裁决恢复工具边界文件同步，并补对应文件读取负向测试。依赖工具的即时撤权已由后续修正恢复。
