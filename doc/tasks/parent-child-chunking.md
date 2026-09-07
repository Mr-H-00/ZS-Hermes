# Parent-Child 切块

状态：已完成

目标：在现有六类切块策略之上，稳定生成父块、子块、offset 和 span。

## 最小任务
- [x] 实现 tokenizer 解析和不可用时的明确拒绝。
- [x] 生成父块完整文本和 Python 字符 offset。
- [x] 在父块范围内生成子块、重叠和 span。
- [x] 保留来源定位信息，不改写原始父块文本。
- [x] 补单测覆盖边界值、跨页 span 和非法参数。

## 完成标准
- [x] 父块大于子块且重叠受限。
- [x] 子块文本和定位信息可回溯。
- [x] 不可用 tokenizer 不会静默降级。

验证：

- `docker compose exec api uv run --no-sync --group test pytest test/unit/plugins/test_parent_child_chunking.py -q`（18 passed）
- `docker compose exec api uv run --no-sync --group test pytest test/unit/plugins/test_ragflow_like_chunking.py test/unit/plugins/test_parent_child_chunking.py -q`（38 passed）
- `docker compose exec api uvx ruff check --no-cache package/yuxi/knowledge/chunking/ragflow_like/parent_child.py test/unit/plugins/test_parent_child_chunking.py`（通过）
- `docker compose exec api uvx ruff format --check --no-cache package/yuxi/knowledge/chunking/ragflow_like/parent_child.py test/unit/plugins/test_parent_child_chunking.py`（通过）

范围：本模块尚未接入入库、PostgreSQL、Milvus 或重切片 workflow；这些由后续模块负责。
