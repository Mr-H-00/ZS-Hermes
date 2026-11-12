# 知识库统计投影的当前 HTTP 回归入口

状态：implemented
类型：testing
Owner：backend/test/integration/services/test_knowledge_stats_refresh.py

## 问题

统计投影的 HTTP 回归用例请求已移除的 `stats/repair` 端点，因此返回 404。当前源码、路由注册和前端没有该修复操作的 consumer；它的路由、manager 和 backend 实现由兼容清理提交 `9ec0d057` 移除。文件操作后的统计刷新和知识库列表的持久投影仍是当前行为，需要沿真实 HTTP 验证。

## 决策

将 HTTP 回归入口改为现有知识库列表。用真实 PostgreSQL 提交文件状态、Chunk 与 Token 计数，经当前 `_run_with_stats_refresh` 聚合并持久化；调用实际路由的 GET 列表，验证返回统计与持久行相同，Redis 中被刻意延长 TTL 的旧统计仍保持原值。应用能力、路由和业务实现不改变。

### 实现方案

测试使用独立 PostgreSQL Schema 和唯一 Redis key，启动真实 Uvicorn 与 HTTP 客户端。仅身份依赖与 uid 可见性入口由 fixture 替换，实际 manager 摘要查询、响应序列化、文件更新、统计聚合和持久回读继续使用真实实现；权限 integration 独立拥有授权证据。原有成功/异常收尾及行锁等待三个案例保持不变。

## 替代方案

- 重新提供已移除的统计修复能力：当前没有 consumer，需要独立产品与数据契约，不为陈旧测试恢复。
- 只删除失败用例：会失去统计 HTTP 投影的直接证据，选择沿当前入口保留它。
- 用 HTTP 200 或 mocked summary 断言替代：无法证明新统计来自持久事实，不采用。

## 后果

回归继续验证统计投影的持久一致性与缓存隔离，不再把已移除能力当作当前契约。测试里的计数是显式文件更新的独立已知结果，未生成 snapshot 或在 CI 自动更新 oracle。

## 验证

`docker compose run --rm --no-deps -e PYTEST_ADDOPTS='-p no:cacheprovider' api uv run --no-sync pytest test/integration/services/test_knowledge_stats_refresh.py -q`：修复前 1 failed、3 passed，失败为旧端点 404；更新后 4 passed，覆盖真实 PostgreSQL、Redis 和 HTTP。

Ruff check 与 format（backend 配置）通过。负向验证在独立进程临时撤销文件操作后的投影刷新，HTTP 测试因返回 Chunk 数为 0、未反映已提交文件的 7 个 Chunk 而失败，撤销保护被检出。完整 Agent assembled path 以更新 head 的 GitHub CI 结果为准。

合入 `829f65fd` 时保留上游新增的退休端点 404 及持久事实不变回归，并独立保留当前 GET 列表的统计投影回归；两个入口分别验证退休能力拒绝和当前能力正确性。
