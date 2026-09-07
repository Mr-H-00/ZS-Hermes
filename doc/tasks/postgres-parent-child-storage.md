# PostgreSQL 父子块存储

状态：已完成

目标：把文档版本、父块、子块和映射关系落到 PostgreSQL，并保持旧单层 chunk 兼容。

## 最小任务
- [x] 新增文档版本、父块、子块表结构和必要索引。
- [x] 实现 `create_staging_version`、`batch_insert_parent_chunks`、`batch_insert_child_chunks`。
- [x] 实现 `activate_version`、`delete_version`、`get_active_version`。
- [x] 保留旧 `knowledge_chunks` 读取路径。
- [x] 补 integration 覆盖写入、激活、删除和外键级联。

## 完成标准
- [x] 只有 PostgreSQL 保存父块真值。
- [x] 子块能通过 `parent_id` 反查到唯一父块。
- [x] 旧单层数据不因新表缺失而失效。

验证：`docker compose exec api uv run --no-sync --group test pytest test/unit/storage/test_postgres_manager_schema.py test/unit/services/test_storage_migration.py -q`（26 passed）；`docker compose exec api uv run --no-sync --group test pytest test/integration/repositories/test_knowledge_parent_child_chunk_repository.py -q`（8 passed）；Ruff 检查通过。共享 PostgreSQL 已由 storage-migrator 升级至 knowledge schema v3，并回读 `knowledge_document_versions`、`knowledge_parent_chunks` 和 `knowledge_child_chunks` 三张表存在。
