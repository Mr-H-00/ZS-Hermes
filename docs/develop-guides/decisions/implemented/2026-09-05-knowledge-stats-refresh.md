# 知识库统计刷新直接聚合持久文件

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/repositories/knowledge_base_repository.py

## 问题

索引结束后刷新统计命中十秒 Redis 读缓存，将旧统计长期保存到知识库列表投影。独立聚合后再锁定写回还允许并发旧快照覆盖新结果。历史文件的 `is_folder` 可为 `NULL`，直接聚合若只匹配 `False` 会漏计文件；虚拟目录迁移新增文件夹行后若不刷新持久投影和文件统计缓存，列表与详情会继续返回迁移前的行数和目录数。

## 决策

知识库仓储在同一 PostgreSQL 事务的行锁内读取文件聚合并写回统计；文件仓储拥有聚合 SQL，读取缓存服务详情、文件列表以及部分 Milvus 和 mindmap 概览读取。聚合把 `is_folder = NULL` 的历史行视为普通文件。修复文件统计后复用统一刷新；虚拟目录迁移完成全部批次后调用知识库仓储刷新持久投影，再通过文件仓储删除该知识库的文件统计短缓存。其他详情读取仍允许十秒延迟，历史投影通过现有修复入口或下一次文件操作更新。

## 替代方案

仅使索引等普通刷新同步改写 Redis 会改变短缓存延迟契约；移除所有读缓存增加大知识库全表聚合开销；新增周期扫描扩大后台任务范围；迁移后不刷新则把可确定修复的旧投影留给后续操作。选用行锁内直接聚合，并只在虚拟目录迁移完成点删除文件统计缓存。

## 后果

每次刷新执行一次真实文件聚合，同库刷新串行。虚拟目录迁移成功后多执行一次持久统计刷新和一次缓存删除；Redis 删除失败按现有可选缓存语义记录告警，读取仍可回源 PostgreSQL。文件提交与刷新仍是两个事务，进程在两者之间被强杀时，Durable Task 重试会再次执行迁移收尾刷新；本变更不增加周期补偿服务。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 操作收尾持久统计来自真实文件 | 缓存返回索引前零值 | knowledge_base_repository.py | PostgreSQL/Redis integration，覆盖成功与异常 | 预置旧缓存，核对提交后统计及列表摘要 | Passed |
| 并发刷新按锁内事实收敛 | 旧快照晚写覆盖 | knowledge_base_repository.py | PostgreSQL integration，使用本测试阻塞连接 PID | 锁等待期间更新文件 | Passed |
| 文件修复返回最新统计 | 修复入口复用旧读缓存 | knowledge/manager.py | 真实 HTTP 修复请求与 PG 回读 | 有旧缓存时修复实际 Chunk 统计 | Passed |
| 历史空值目录标记按文件聚合 | `is_folder = NULL` 的文件从数量、容量和状态桶消失 | knowledge_file_repository.py | PostgreSQL integration，直接聚合空标记记录 | 将真实文件标记恢复为 `NULL` | Passed |
| 虚拟目录迁移后两份统计立即收敛 | 持久投影或 Redis 仍保留迁移前目录数 | knowledge_folder_service.py | unit 调用顺序与 PostgreSQL/Redis integration 回读 | 预热旧缓存后迁移路径型文件 | Passed |

执行：`docker compose exec -T api python -m pytest test/integration/services/test_knowledge_stats_refresh.py -q -p no:cacheprovider`，6 个用例通过。原实现下成功与异常收尾均因 `chunk_count=0` 失败，并发用例因未计入等待期间的更新失败。该文件由 `.github/workflows/system-tests.yml` 执行。

负向验证在独立测试进程中将 `refresh_stats` 替换为锁前读取缓存再写回的实现：4 个既有用例分别因 `0 != 158`、`0 != 158`、`0 != 7`、`0 != 1` 失败。临时恢复只匹配 `is_folder = False` 并移除迁移收尾刷新后，两个新增用例分别因文件数量、容量、Chunk、Token 和状态桶归零，以及持久投影缺少 `stats` 失败；恢复实现后 6 个用例再次通过。

回归执行 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`：1770 passed、50 skipped。标准命令的依赖同步因容器 site-packages 写权限失败，使用现有已安装依赖执行测试。工程契约检查及其 61 个单测、Ruff、`git diff --check` 和 `cd docs && pnpm run build` 通过；文档构建有 VitePress/Rolldown 兼容警告。

HTTP 用例装配真实知识库路由，认证依赖由 fixture 替换；统计修复使用真实 PostgreSQL Chunk 和 Redis，Milvus 连接初始化被替换。完整 PDF/OCR、嵌入模型和 ARQ worker 入库 E2E 为 Not run；索引执行器本身未改动。
