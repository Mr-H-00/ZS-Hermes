# 移除重复的 Knowledge external API

状态：implemented
类型：simplification
Owner：backend/yuxi/api/routers/public_v1/knowledge.py

## 问题

Knowledge external 与 Public 工具接口重复提供列库、文件搜索、检索、打开和查找能力，却使用不同的输入契约。CLI 依赖 external，直接删路由会使已有命令失效。

## 决策

### 实现方案

移除 external 路由模块、注册和 Knowledge Key 的 external 前缀授权。CLI 五个知识库命令迁到 `/api/v1/knowledge/tools`，与 Agent 使用同一服务。`search_file` 的共享输入增加可选 `kb_id`，保留 CLI 按 ID 列文件；服务在当前可见集合内定位 ID，同时传名称时进一步收窄，不绕过用户或会话权限。

此次移除 external 的自由 `options` 入口；`query_kb` 继续使用知识库保存的检索配置和可选文件名过滤。管理侧检索测试参数的统一属于后续独立工作。无持久数据迁移，不新增兼容路由。

## 替代方案

- 保留两组接口：继续承担重复契约与授权维护成本。
- 缩减为 external 到工具服务的转发：仍保留用户明确要求移除的 API 表面。
- 移除 external 并迁移调用方：采用；已安装的旧 CLI 和外部集成需要升级调用地址。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| external 不再注册或授权 | 遗留路由或前缀授权 | Public router、auth dependency | 源码引用搜索与 HTTP 路由回归 | 请求旧五类操作不可成功 | Not run：HTTP 被运行环境 Schema 版本阻断 |
| CLI 五个命令调用工具接口 | 方法、请求体或返回结构漂移 | CLI client | client 协议回归 | 错误路径或缺失 kb_id 使断言失败 | Passed：CLI typecheck、9 项协议测试和 pack 检查 |
| 按 ID 搜索只读取可见库 | 跨库或会话越权 | Knowledge tools service | 工具与 HTTP 权限回归 | 不可见 ID 即使名称可见也拒绝 | Passed：工具 unit；Not run：HTTP 被 Schema 版本阻断 |

旧能力不存在：已检查运行时源码，无 `databases/external`、`external_kb` 或删除模块的导入。旧地址只保留在移除回归用例与历史决定中。`git diff --check` 通过。

重新引入条件：只有出现工具契约无法承载的独立业务语义并另行决策，才考虑新的公开能力。

## 后果

这是公开 API 的破坏性删除；旧 external 调用方必须迁移。真实 HTTP 与检索 E2E 尚未验证。当前运行环境知识库 Schema 为 v3，独立提交快照要求 v2，HTTP integration 在 Schema 前置检查被拒绝；不修改运行环境版本或绕过门禁。

本决定取代 [Knowledge Public v1 边界](2026-09-28-knowledge-public-v1.md)中的 external 双入口与兼容窗口，并更新 [npm CLI](2026-10-01-npm-cli-public-v1.md)的知识库调用路径。CLI 列库直接返回工具的数组结果，不再返回 external 的 `databases` 包装及类型能力字段；文件列表采用工具的文件字段，不额外返回 external 的文件夹字段。

提交快照验证：后端 unit 使用已有容器依赖与快照 PYTHONPATH，2518 passed、55 skipped；标准 uv 同步被容器 yuxi.egg-info 时间戳权限阻断。CLI 执行 `npm run typecheck`、`npm test`（9 项通过）与 `npm run pack:check`。工程契约及其 64 项测试、文档 build 与补丁空白检查通过。HTTP integration 实际执行后在前置检查报 `knowledge=3 (required 2)`，未进入业务断言；worker/Milvus E2E 因相同版本不兼容未执行。
