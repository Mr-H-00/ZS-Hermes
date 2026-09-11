# Knowledge 带时区字段使用 aware UTC

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/storage/postgres/models_knowledge.py

## 问题

Knowledge 与 Evaluation 模型的 `DateTime(timezone=True)` 字段曾由 naive UTC datetime 提供默认值和显式更新时间，Parent-Child 激活与 Knowledge 文件执行 Owner 收敛也存在相同写入。在非 UTC PostgreSQL 会话中，驱动或数据库会按会话时区解释无时区值，使持久化时刻偏离真实 UTC；Evaluation 从无时区的 Task 终态复制完成时间时也需要显式恢复 UTC 语义。

## 决策

所有 Knowledge Schema 中 `DateTime(timezone=True)` 字段的 Python 默认值、自动更新时间和显式写入统一使用 aware UTC datetime；数据库迁移直接使用 PostgreSQL `now()` 写入绝对时刻。Evaluation 复制 `TaskRecord.completed_at` 时，将该业务表中按 UTC 保存的 naive datetime 标记为 UTC 后再写入带时区字段。

该修复不修改列类型、不迁移既有数据，也不提升 Knowledge Schema 版本；PostgreSQL 的 `timestamp with time zone` 继续拥有持久化时刻。

## 替代方案

- 依赖所有 PostgreSQL 连接固定使用 UTC：拒绝。字段正确性不应依赖会话配置。
- 把 Knowledge 时间列改为无时区类型：拒绝。会扩大 Schema 迁移与所有读取者的解释范围。
- 只修复 Evaluation 的显式写入：拒绝。ORM 默认值与 KnowledgeFile 更新时间仍会保留相同缺陷。

## 后果

新写入的 Knowledge 与 Evaluation 带时区字段包含明确 UTC offset，并在不同 PostgreSQL 会话时区下表示同一时刻。既有持久化值和 Schema 版本不变；无时区业务字段仍继续使用 `utc_now_naive()`，不会被本决定整体改写。

## 验证

- `docker compose -p yuxi-merge-v073-test exec api uv run --group test pytest test/integration/repositories/test_knowledge_parent_child_chunk_repository.py test/integration/services/test_schema_migration_version.py -q`：22 passed；真实 PostgreSQL 临时 Schema 覆盖非 UTC 的模型默认值、Parent-Child 激活和文件 Owner 收敛，并从 UTC session 回读持久化时刻。
- `docker compose -p yuxi-merge-v073-test exec -e RUFF_CACHE_DIR=/tmp/yuxi-ruff-cache-final api uv run ruff check package/yuxi/knowledge/implementations/milvus.py package/yuxi/repositories/knowledge_parent_child_chunk_repository.py test/unit/knowledge/test_milvus_parent_child_index_flow.py test/integration/repositories/test_knowledge_parent_child_chunk_repository.py test/integration/services/test_schema_migration_version.py`：通过。
