# Milvus 子块集合

状态：已完成

目标：把向量检索写入和查询切到子块集合，父块只留在 PostgreSQL。

## 最小任务
- [x] 定义按维度命名的子块 collection schema。
- [x] 写入 dense、sparse 和 BM25 需要的字段。
- [x] 查询时按知识库配置选择单层或 Parent-Child executor。
- [x] 让旧 collection 继续可查，但不把父块写进 Milvus。
- [x] 补 integration 覆盖缺字段、错误维度和旧数据兼容。

## 完成标准
- [x] 子块集合能独立承载检索。
- [x] 父块不会进入 Milvus。
- [x] 缺少 `parent_id` 或非法 sparse 配置会失败。

验证：

- `docker compose exec api uv run --no-sync --group test pytest test/unit/knowledge/test_milvus_child_collection.py -q`（8 passed）
- `docker compose exec api uv run --no-sync --group test pytest test/integration/knowledge/test_milvus_child_collection_integration.py -q`（4 passed）
- `docker compose exec api uv run --no-sync pytest test/integration/knowledge/test_milvus_single_chunk_sparse_integration.py -q`（1 passed）
- 真实 Milvus schema/data 回读通过：`rag_child_chunk_<dimension>` 含 `child_id`、`parent_id`、`child_text`、dense、BGE sparse 与 BM25 字段；父块未写入。
- 新建单层集合也固定包含 BGE sparse 字段和索引；开启时回读 provider 的真实 sparse 映射，关闭时回读空映射。旧单层集合选择路径和缺字段/错误维度/非法 sparse 负向案例通过。

范围：当前 Milvus 模块提供集合和写入边界；完整 Parent-Child 入库、父块回读和查询聚合由后续模块负责。
