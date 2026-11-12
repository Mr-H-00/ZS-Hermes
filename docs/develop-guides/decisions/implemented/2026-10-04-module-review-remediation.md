# 模块审查中的失败语义与检索边界

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/base.py

## 问题

对 `backend/yuxi/modules` 的审查报告混合了真实功能缺陷、已经由当前入口拦截的陈旧判断和结构性债务。复核确认：知识库导出基类会落入空实现；Dify/Notion 外部检索在远端失败时返回空结果；Milvus/Dify 的检索数量上限只停留在 UI 配置；损坏的共享权限配置会让 Agent/Skill 权限遍历抛异常，错误类型的空值还会被当作 global 授予读取或管理权限；MCP 服务层缺少与 HTTP 层对称的内置保护；评估 prompt 使用了字面 `\\n`。这些行为分别会伪装失败、放大下游查询、扩大单条坏数据影响面或产生错误模型输入。

复核也确认：workspace 文件服务的列表过滤是“Project 选目录”的展示投影，读写边界是当前用户自己的 UserWorkspace；Notion 文件方法虽然与可选参数接口不一致，但当前 shipping 入口会先拒绝不支持文档操作的外部连接器；权限解析器拒绝 legacy 配置本身是 fail-closed 设计。因此本次不改变这些语义，也不进行 transport/sandbox 下沉、事务约定统一或死代码大清理。

## 决策

基类 `KnowledgeBase.export_data` 显式抛出 `NotImplementedError`，沿用已有路由的 501 映射，避免把未实现能力伪装为普通 500。

Dify 与 Notion 的远端请求失败向上抛出；保留 Dify 的兼容重试，但两次失败后不返回空检索结果。Dify 的返回结果按受保护的最终数量截断。

在实际执行检索前执行与公开配置一致的运行时上界：Dify 最终结果 100；Milvus 最终结果 100、召回 200、BM25 200、图实体/三元组 100、图结果 200、图节点 50000。主检索路径的非法数值显式失败，超出合法范围的数值收敛到安全上限。可选图检索保留现有异常降级：图参数解析或图服务失败时记录错误并返回空图结果，主检索继续返回。

权限解析遇到共享配置解析产生的 `TypeError` 或 `ValueError` 时记录结构化告警并返回 `NONE`，不让单条坏资源中断 Agent/Skill 列表；`normalize_permission_config` 在共享配置解析边界拒绝非字符串访问级别、非列表成员、布尔或浮点部门 ID、非字符串用户 UID；省略、None、空字符串访问级别仍沿用既有 global 默认，合法整数字符串部门 ID 继续支持。Agent 序列化对 superadmin 保留损坏配置原值供修复；Skill 描述保留合法配置的规范化读取契约，仅在解析失败时保留原始坏配置；权限判定仍由 repository/resolver 拥有，避免管理列表在重复解析时整页失败。读取投影通过 share_config_invalid 明确报告无效状态。前端显示修复提示，在本地暂设仅所有者范围，用户可重新选择并明确保存后替换持久化原值；打开页面不写数据库。Agent 编辑以 agent_id/slug 提交更新，不使用数据库数字主键。MCP 删除服务在真正删除前拒绝代码管理的内置 slug，HTTP 层现有保护继续保留。评估上下文使用真实换行连接。

知识库读取投影对损坏配置保留原值，绝不把 SQL NULL / JSON null 改成 global；共享 resolver 拒绝非 superadmin 访问，superadmin 仍能修复和删除。创建入口传入 None 的既有合法全局默认不变。共享 Skill 的 null 原值也明确标记为无效，个人 Skill 的 None 保持原语义。统计与分页测试只在需要正常共享的 fixture 显式指定合法配置。

### 实现方案

执行入口仍保持 `router → knowledge manager → concrete knowledge backend`，边界校验在 concrete backend 的 `aquery` 内完成；故障通过异常返回到已有 API 错误映射，PostgreSQL、Redis 和外部对象不新增状态。配置类型 guard 位于共享 normalizer，保存入口显式拒绝；共享 resolver 捕获坏配置，Agent、Skill 和知识库可见性查询复用同一 fail-closed 结果。MCP guard 位于 service owner，不能只依赖 HTTP route。

## 替代方案

- **实现完整知识库导出**：需要定义各 backend 的格式、向量和文件语义，超出本次审查修复；选择明确返回 501。
- **继续把外部失败转换为空列表**：会让“服务不可用”和“没有命中”不可区分，违反显式失败约定，不采用。
- **只修改 UI 的 max 字段**：无法约束 API、Agent 或持久化历史配置，不采用。
- **在每个 Agent/Skill 调用方分别捕获权限异常**：会产生遗漏和不同安全语义，选择在共享 resolver 统一收口。
- **立即重构模块依赖和工作区授权模型**：需要独立架构决策与真实链路验证，本次不纳入。

## 后果

外部连接器故障从空结果变为请求失败，调用方会显示错误而不是继续生成答案；这是有意的可观察行为变化。上界会截断超过 UI 声明范围的调用参数，但保护 Milvus/Dify 资源。Malformed 权限数据会使共享 resolver 对非 superadmin 返回 NONE，普通资源不可见直到配置被修复；内置 Skill 的既有管理员管理入口保持独立的角色策略。这是 fail-closed 后果。完整知识库导出仍未实现，接口明确返回 501。

## 验证

- NULL 投影负控：修复前 SQL NULL、JSON null 两种知识库在普通用户列表仍可见，共享 Skill null 未标记无效，新增三项真实 HTTP/PG 测试在正确断言失败。修复后管理员读取合法全局库成功，损坏后访问 403、列表和工具均不可见；superadmin 可删除，独立 PG 回读确认不存在。共享 Skill null 保留原值、标记无效并可修复。

- 管理读取负控：真实 PostgreSQL 写入坏配置后，修复前四项 Agent superadmin 列表请求和一项 Skill 卡片请求均返回 500；修复后列表与详情能展示原值，管理员通过真实 HTTP 更新为合法配置，独立回读 PostgreSQL 并证明普通用户恢复读取。共享 Skill 编辑回归同时运行。
- 前端 guard：五项共享配置/真实 Agent 编辑组件 unit 覆盖安全暂存、响应式克隆、原值不变、明确保存和 slug；在独立进程恢复数字 ID 与撤销坏配置安全暂存，两种变异均被业务结果断言检出。真实浏览器验证 Agent 编辑和 Skill 配置能打开、警告可见、读取/管理开关关闭，打开后独立 PostgreSQL 回读仍为原坏配置，点击保存后两行均为仅所有者。浅色桌面与深色 768px 视口保留截图；390px 默认侧栏下存在页面横向溢出，不扩大本次样式修改范围。
- 配置类型负控：修复前新增的 18 项 `test_invalid_resource_permission_config_denies_access` 因错误授权或未捕获异常失败，修复后通过；合法默认访问级别和整数字符串部门 ID 另有正向回归。
- `test/integration/api/test_module_failure_boundaries.py`：真实 PostgreSQL 损坏 Agent 记录在普通用户列表中隐藏，直接读取和修改返回 404，保存错误配置返回 422 且不新增持久行；正常记录仍可读取。内置 MCP 的 HTTP 与直接 service 删除均拒绝，独立回读完整数据库行保持不变。
- 同一 integration 文件通过本地确定性 Dify HTTP 对端记录实际请求：远端两次 503 后 query-test 返回 500，query 返回 failed；超限请求发送 top_k=100，201 条协议结果只返回前 100 条；未实现导出通过真实 HTTP 返回 501。协议对端不替换生产客户端、manager 或数据库。
- 新 integration 文件已接入 `.github/workflows/system-tests.yml` 的 Runtime job；CI 使用当前提交的 API、真实 PostgreSQL 与独立协议响应执行 guard。
- 真实 Compose integration：`pytest test/integration/api/test_module_failure_boundaries.py test/integration/services/test_knowledge_stats_refresh.py test/integration/api/test_shared_skill_edit_router.py -q`，20 passed；使用隔离 review 拓扑和本工作树源码，不使用用户长驻运行环境。
- Backend unit：`docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow" -q`，2507 passed、55 skipped；skip 不代表验证通过。真实 Milvus 3 worker E2E：`pytest test/e2e/test_milvus_text_retrieval_e2e.py -q`，1 passed，覆盖建索引、vector/keyword/hybrid、文本约束与删除后的持久状态。PR 最新 head 的完整 CI 另由执行报告记录。
- 前端 lint、unit 与 typecheck/build 通过；使用仓库同一脚本的直接 Node 入口复用工作树依赖，最终计数见 PR 执行报告。
- 原有 connector、export、evaluator、MCP 和 Milvus 检索边界 unit 保留。原实现提交记录的八种检索边界变异与缺字段回归作为历史证据；本次独立 review 直接核对实际 executor 参数和最终截断，并新增上述 HTTP oracle。
- 工程契约通过；`python3 -m unittest scripts.test_verify_engineering_contracts scripts.test_release_workflows`，72 passed；变更 Python 的 Ruff check/format 与 `git diff --check` 通过。文档以 `node node_modules/vitepress/bin/vitepress.js build` 构建并检查相对链接，通过。当前 pnpm 会尝试替换跨工作树依赖符号链接而拒绝执行，直接调用同一 build 脚本复用已装依赖。

真实 Dify/Notion SaaS 与外部图谱 provider 未运行；Dify 协议故障和超限响应使用本地 HTTP 对端。Milvus 使用隔离拓扑的真实服务；首次 E2E 因缓存镜像的 PyMilvus 3.0.1 与仓库锁定 3.0.2 不符失败，API/worker 对齐已锁定的 3.0.2 并重启后通过，未改变测试断言。容器内 canonical `uv run --group test` 因既有 root 所有的 editable 包元数据不能更新时间戳而失败；复用已装依赖的 `--no-sync` 执行同一 pytest 集合，干净构建由 CI 验证。
