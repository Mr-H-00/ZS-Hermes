# Parent-Child 图谱、重切片与存储兼容

状态：implemented
类型：architecture
Owner：backend/package/yuxi/knowledge/manager.py

## 问题

Parent-Child 需要同时定义切块、版本切换、Milvus 检索、PostgreSQL 正文、Neo4j 映射和旧单层 chunk 的边界。缺少统一约束时，新版本可能提前覆盖旧数据，图谱可能从相邻记录猜测父子关系，外部清理失败可能丢失重试所需的 PostgreSQL Owner，或稀疏检索在 provider 不支持时静默降级。

## 决策

1. Parent-Child 是独立开关；关闭时配置规范化忽略不生效的父子参数并恢复稳定默认值，索引继续使用既有单层 chunk 链路，不改写历史 collection 和图谱语义。
2. PostgreSQL 持有文档版本、父块正文和父子映射。Milvus 按 embedding 维度共享 `rag_child_chunk_<dimension>` collection，只保存带 `kb_id`、`file_id`、`version_id` 和 `parent_id` 的子块检索投影。
3. 新版本按 `staging -> PostgreSQL 父子块 -> Milvus 子块 -> flush/read-back -> active` 发布。发布前失败会删除新版本两边的数据，原 active 版本保持可见。
4. 重切片只能由显式 API 提交到 Tasker；等价请求用 fingerprint 去重。文件归属与管理权限在后端边界执行。
5. 检索只读取 PostgreSQL 当前 active 版本对应的 Milvus 子块，按 child 分数融合和重排，再按 `parent_id` 聚合并从 PostgreSQL 回读父块正文。
6. Neo4j 以 `ParentChunk` 为语义主体，通过显式 `HAS_CHILD` 指向 `ChildChunk`。映射缺失或错配时拒绝写入，不从顺序或相邻记录推断。
7. BGE-M3 sparse 仅在模型能力匹配且 provider 返回真实 sparse 数据时启用；不写零向量伪装成功。
8. Redis 只缓存带知识库和活动版本作用域的父块与查询结果；异常和 miss 回到 PostgreSQL/Milvus，版本切换后旧缓存不能成为事实来源。
9. 文件图谱与 superseded 父子版本的清理先从 PostgreSQL 只读计算外部删除目标，再幂等删除 Neo4j 和 Milvus 投影；两种外部删除全部成功后才删除 PostgreSQL 引用与孤儿记录。任一外部失败直接传播，并保留同一请求重试所需的 PostgreSQL 状态。
10. 文件内容回读与统计修复都按 active Parent-Child 版本读取父块，不再从 legacy `knowledge_chunks` 反推 chunk_count 或 content lines。

## 替代方案

- 直接替换 `knowledge_chunks`：拒绝。它会破坏旧数据和既有 consumer。
- 在单个 collection 混合父块与子块：拒绝。父块正文和检索投影的 Owner 会变得不明确。
- 配置变更后自动重切片：拒绝。它会在没有用户显式动作时改变持久数据。
- provider 缺少 sparse 输出时写占位数据：拒绝。该结果会把能力缺失伪装为成功。
- 先删除 PostgreSQL 引用再清理外部投影：拒绝。外部删除失败后无法重新计算孤儿向量目标，同一清理请求不可重试。

## 后果

- 系统同时维护旧单层链路与新父子链路，但选择点由规范化配置唯一决定。
- Parent-Child 写入需要 PostgreSQL 与 Milvus 的补偿式回滚；两者不是分布式事务，因此 read-back 是激活前的发布门槛。
- 图谱与检索都依赖显式父子 ID，存储和测试成本增加，但不会从相邻 Run、chunk 或版本猜测结果。
- collection 按维度共享，删除和查询必须始终带知识库及版本作用域。
- 图谱清理不是跨存储事务；Neo4j 已成功而 Milvus 失败时，重试会重复执行幂等 Neo4j 删除，PostgreSQL Owner 在外部投影全部成功前保持不变。

## 运行时约束补充

- 子块窗口优先在配置的 separator 后收束；最后一个窗口始终覆盖父块尾部，避免 separator 与 overlap 组合造成原文丢失。
- RRF 分数是基于排名的倒数，不与相似度同量纲。启用 RRF 时，similarity_threshold 在各候选列表进入 RRF 前过滤原始分数，RRF 后不再用该阈值过滤融合分数。

## 验证

- 配置、API、storage、Milvus、切块、检索、图谱、缓存和前端均有负向 unit。全部 1767 个后端 unit 由两个隔离命令覆盖：主分片 `1686 passed, 44 skipped`，既有 `test_run_worker.py` 独立分片 `37 passed`。
- 非 API integration 为 `75 passed, 4 skipped`，覆盖真实 PostgreSQL、Milvus、Neo4j、Redis 和 service；Parent-Child HTTP integration 为 `2 passed, 40 skipped`，跳过项需要通用 integration 登录凭据。
- PostgreSQL repository integration 覆盖唯一 active、级联删除、旧 `knowledge_chunks` 共存，以及成功激活和 read-back 失败后保留旧 active、清理新版本。共享 knowledge schema 已迁移至 v3，并回读三张父子表存在。
- 真实 Neo4j integration 覆盖 ParentChunk、ChildChunk、`HAS_CHILD`、active 版本过滤和知识库隔离删除；unit 还覆盖文件删除与 superseded 版本清理装配。
- unit 还覆盖 Parent-Child 文件的 content 回读与 stats repair 分流，确保回读父块表而不是旧 `knowledge_chunks`。
- 前端单元测试覆盖可选开关、字段互斥、BGE-M3 能力限制、停用参数载荷和知识库类型切换，lint 与 production build 通过；真实 Vite 页面在 1440x1000 和 500x900 视口检查 Parent-Child 开启与关闭状态，字段标签、开关状态和响应式布局符合配置语义。
- 新增 Parent-Child Owner 通过配置化 mypy；Ruff lint、本任务 Python 文件 formatter、工程信任检查及其 61 项 unittest、Node 24 临时容器中的 VitePress build 和 `git diff --check` 均通过。本机 Node 20 运行 pnpm 11 受 `node:sqlite` 版本约束，未作为通过依据。
- 图谱定向 unit 为 `46 passed`，覆盖 file graph 和 superseded version 的 Milvus 删除失败重试、Neo4j 删除失败短路及 collection drop 异常传播；包含 PostgreSQL repository 的定向 integration 为 `11 passed`，通过隔离 PostgreSQL Schema 回读确认删除目标预演不修改引用、实体或三元组，并覆盖真实 Milvus 与 Neo4j。
- 完整 `test/integration` 单命令在凭据跳过后的 API 子套件边界无界等待，未记为通过；完整 `test/unit -m "not slow"` 单进程也在既有 worker 测试的 pytest-asyncio teardown 顺序点无界等待，全部 unit 改由两个隔离命令覆盖。全包 formatter 仍命中未修改的既有 `knowledge/parser/mineru.py`，本任务变更文件已通过。也未使用真实外部 embedding/rerank provider 执行完整查询 E2E。
