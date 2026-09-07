# 总体进度

状态：已完成

## 模块进度
- [x] `config-normalization`
- [x] `api-contract`
- [x] `postgres-parent-child-storage`
- [x] `milvus-child-collection`
- [x] `parent-child-chunking`
- [x] `ingestion-reslice-flow`
- [x] `retrieval-query-path`
- [x] `graph-mapping`
- [x] `redis-cache`
- [x] `frontend-parent-child-ui`

## 最终验证

- 后端 unit 已拆分覆盖全部 1767 项：排除既有 worker teardown 顺序问题的主分片为 `1686 passed, 44 skipped`，`test_run_worker.py` 独立为 `37 passed`。
- 真实 PostgreSQL、Milvus、Neo4j、Redis 与 service integration：`75 passed, 4 skipped`；Parent-Child HTTP integration：`2 passed, 40 skipped`，跳过项需要通用 integration 登录凭据。
- 前端最新宿主测试目录通过只读挂载执行：`178 passed`；`lint:check` 与 production build 通过。
- `ruff check package` 通过；本任务变更的 Python 文件通过 `ruff format --check`。
- `uv run --frozen --group dev mypy` 通过，检查 5 个新增 Parent-Child 语义 Owner。
- 工程信任检查通过，配套 unittest 为 `61 passed`；VitePress docs build 通过；`git diff --check` 通过。
- storage-migrator 已将共享 PostgreSQL 的 knowledge schema 升至 v3，且三张 Parent-Child 表均已回读确认存在。
- `/api/system/ready` 返回 HTTP 200，`degraded=false`。

## 未验证范围

- 完整 `test/integration` 单命令在 `test_dataset_generation_resume_router.py` 按凭据条件跳过后、下一个 API 用例开始前无界等待；已拆分验证本任务 HTTP 与全部非 API integration 目录，不能把完整单命令记为通过。
- 完整 `test/unit -m "not slow"` 单进程在既有 `test_run_worker.py` 的 pytest-asyncio teardown 顺序点无界等待；全部用例已由“排除该文件的主分片 + 该文件独立运行”覆盖，不能把单进程命令记为通过。
- 全包 `ruff format package --check` 仍会命中未修改的既有 `knowledge/parser/mineru.py`；本任务所有 Python 变更文件已单独通过 formatter gate。
- CUA 未提供可用浏览器且内置浏览器不可用，未执行真实页面截图检查。
- 未使用真实外部 embedding/rerank provider 执行完整 Parent-Child 查询 E2E；检索编排由 deterministic unit、真实 PostgreSQL/Milvus 回读和真实 Neo4j 过滤分别证明。
