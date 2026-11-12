# 知识库可见范围的领域归属与执行时授权

状态：implemented
类型：architecture
Owner：backend/yuxi/modules/knowledge/services/access.py

## 问题

Agent Context 的知识库解析混合用户权限查询、会话选择和运行态写入。知识工具复用 Context 快照，权限撤销后仍可继续读取；未准备的 `"all"` 选择被当成字符列表筛选。

## 决策

### 实现方案

知识库 service 接收用户 ID 与 `"all"` 或显式 ID 列表，调用现有 Manager 的用户可见性查询后取选择交集，只返回工具所需摘要。identity 的资源权限谓词与知识库 repository 保持事实 Owner；service 不接收或修改 Agent Context。

Context 准备仅保存已归一化的知识库 ID，不生成无人消费的知识摘要缓存或重复查询权限；知识工具在每次调用时按当前用户权限与会话选择重新解析，不将准备快照作为执行授权。Public API 复用同一 service。查询失败向调用边界传播，不伪装成空权限范围。Agent runtime 的知识解析模块已删除。本决定部分取代[Agent runtime 边界决定](2026-09-30-agent-runtime-boundaries.md)中的知识解析归属。

目标是统一范围解析并证明撤权生效；不改变共享权限协议、Run 生命周期、沙盒与持久化模型，不引入权限缓存或兼容入口。假设同进程 Context 身份由既有执行入口拥有，工具选择服从当前资源选择协议。

## 替代方案

- 保留 runtime helper：知识工具依赖 Agent 资源装配，快照继续承担授权。
- 原样迁入 knowledge：领域服务仍修改 Agent 私有字段。
- 合并进 context.py：便于定位上下文写入，但共享权限查询仍由 Agent 运行层提供。
- 在 identity 新建知识查询服务：权限谓词与知识摘要、选择用例混在一起。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| all、固定列表、空列表只返回当前可读范围 | 字符筛选、越权 ID 或空选择扩大范围 | knowledge/services/access.py | access unit | 不可见 ID、无身份、非法选择 | Passed |
| 工具执行不依赖旧权限快照 | 撤权、伪造缓存或修改选择仍能读取 | extensions/tools/knowledge/tools.py | 工具 unit、真实 PostgreSQL 与 HTTP integration | 撤权后列举与查询被拒绝 | Passed |
| Context 准备只装配范围，Public API 共享领域入口 | 更换位置改变准备结果或 HTTP 错误分类 | runtime/context.py、Public Knowledge router | Context unit、HTTP unit 与 integration | 不存在用户、存储失败 | Passed |
| 当前 consumer 不依赖旧解析模块 | 遗留 import、mock 或文档 | 调用方与当前文档 | 符号搜索、工程契约、docs build | 旧模块不存在 | Passed |

## 后果

每次工具调用增加当前权限读取。Context 的 ID 选择保留用于运行态装配，执行结果可因权限撤销而收窄。Context 不保存知识摘要，避免无 consumer 的缓存和重复权限查询。授权查询与实际读取之间仍沿用现有系统的事务边界，不承诺跨远端读取的原子撤权。全工作树包含其他未提交调整，验证与 Review 区分本次补丁和既有变更。

## 验证命令与范围

- `docker compose exec -T api uv run --group test pytest test/unit/services/test_knowledge_access.py test/unit/toolkits/test_kbs_tools.py test/unit/agents/test_context_auth.py test/unit/routers/test_public_knowledge_tools_errors.py test/integration/api/test_knowledge_access_resolution.py -q --tb=short -p no:cacheprovider`：62 passed。选择交集、all、空选择、缺失身份、空知识库与非法选择由 service unit 覆盖；工具 unit 保留旧权限快照与查询失败回归；集成仅创建一个临时知识库，通过 HTTP 撤权后回读 PostgreSQL、Agent 工具拒绝结果与 Public HTTP 404，结束后清理数据。
- 初次实现前，旧权限缓存与 all 字符筛选的回归案例在结果断言处失败。测试使用自建资源，不依赖当前环境已有知识库或真实检索模型。
- 最终完整工作区执行后端全量 unit：2280 passed、55 skipped。Skill middleware fixture 显式填入生产快照所需工具依赖，worker unit 替代无关的 checkpoint 读取；保留原业务断言，生产行为不变。完整命令由[文档解析验证](2026-09-30-document-parsing-artifacts.md#验证)记录。
- 最终完整工作区的工程契约、64 项 verifier unit、docs build 与 `git diff --check` 通过。相关新测试 Ruff check 通过。
- 未执行 worker/模型 E2E或实际检索；当前直接证据覆盖知识范围解析、真实 HTTP 与 PostgreSQL 撤权。
