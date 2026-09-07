# 图谱映射

状态：已完成

目标：把 Parent-Child 关系映射到图谱写入和命中回读里。

## 最小任务
- [x] 定义父块为语义主体、子块为下属节点的映射规则。
- [x] 让图谱命中能反查到 `child_id`。
- [x] 保留旧 chunk 图谱路径，不改变历史关系。
- [x] 补 integration 覆盖父块、子块和来源定位回读。
- [x] 补负向用例：图谱结果缺少本次父子标识时拒绝当成新结果。

## 完成标准
- [x] 图谱结果可定位到父块和子块。
- [x] 旧图谱数据仍可读。
- [x] 不会用相邻记录猜测命中关系。

## 实现边界

- Parent-Child 写入使用独立的 `ParentChunk` / `ChildChunk` 节点和 `HAS_CHILD` 边；父块承载实体语义，子块只保存可回溯的定位身份。
- 图谱 PPR 先返回 `parent_id`，再通过 PostgreSQL 按显式父子关系展开为 `child_id` 候选；缺少 `parent_id`、子块或父子错配时失败，不从邻近记录猜测。
- 当前 active `version_id` 在 PPR 和 `top_k` 截断前下推到 Neo4j，superseded 节点不能挤占候选窗口。
- 现有 `Chunk` 节点、`chunk_id` PPR 和历史删除路径保持兼容；文件删除同时清理新的父子节点。
- 文件删除、知识库删除和版本激活后的 superseded 清理均覆盖 Neo4j 图节点及图向量投影。

## 验证

- `docker compose exec -T api uv run --no-sync pytest test/unit/graphs/test_milvus_graph_build.py test/unit/graphs/test_parent_child_graph_mapping.py -q`：46 passed。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/graphs/test_parent_child_graph_mapping.py test/integration/graphs/test_milvus_graph_delete.py -q`：4 passed。
- 与 PostgreSQL repository 联合的定向 integration 为 11 passed；真实回读证明删除目标预演不修改 PostgreSQL 引用，并覆盖 active 版本过滤及删除目标知识库而保留邻接知识库。
- 文件和 superseded 版本均按 PostgreSQL 预演、Neo4j、Milvus、PostgreSQL finalizer 的顺序清理；外部失败负控证明 Owner 保持可重试。
