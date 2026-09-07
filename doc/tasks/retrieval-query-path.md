# 检索查询路径

状态：已完成（代码与 focused unit 已验证）

目标：把单层检索、向量融合、RRF、Rerank 和父块聚合串成固定顺序。

## 最小任务
- [x] 按配置选择单层 executor 或 Parent-Child executor。
- [x] 实现 dense/sparse 的 Min-Max 归一化和权重融合；缺少 sparse provider 时 fail-closed。
- [x] 实现常数为 `60` 的 child 粒度 RRF，并在 Rerank 前执行。
- [x] 在 Rerank 后按 `parent_id` 聚合，按最高 child 分数保留父块并回读完整 `parent_text`。
- [x] 补单测覆盖缺分支、单值 Min-Max、RRF、重复父块、旧路径兼容和父块回读。

## 完成标准
- [x] RRF 永远发生在 Rerank 之前。
- [x] 返回结果始终绑定本次命中的父子关系，并保留 child 定位信息。
- [x] 旧单层查询格式保持兼容。

## 验证证据

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit/knowledge/test_milvus_parent_child_query.py test/unit/knowledge/test_milvus_parent_child_retrieval.py -q`：11 passed。
- 完整后端 unit 中 `test_milvus_single_chunk_features.py` 8 项、`test_milvus_kb.py` 25 项与 `test_milvus_child_collection.py` 8 项通过，覆盖单层与 Parent-Child 的 sparse、RRF、失败传播和旧路径兼容。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/knowledge/test_milvus_child_collection_integration.py -q`：4 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/repositories/test_knowledge_parent_child_chunk_repository.py -q`：8 passed。
- `docker compose exec -T api uv run --no-sync --group test python -m py_compile package/yuxi/knowledge/implementations/milvus.py test/unit/knowledge/test_milvus_parent_child_retrieval.py`：通过。
- 尚未使用真实外部 embedding/rerank provider 执行 Parent-Child 端到端查询；本次已分别验证两个存储 Owner 的真实写入、查询和回读，并以 deterministic unit 验证完整编排。
