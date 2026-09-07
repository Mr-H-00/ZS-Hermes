# 知识库稀疏-稠密向量与 Parent-Child 检索详细设计

状态：详细设计
类型：feature / architecture
输入：[`doc/proposal.md`](proposal.md)
相关决策：[`docs/develop-guides/decisions/implemented/2026-09-05-parent-child-graph-reslice-and-storage-compatibility.md`](../docs/develop-guides/decisions/implemented/2026-09-05-parent-child-graph-reslice-and-storage-compatibility.md)

## 1. 设计目标与边界

本文档把需求提案收敛为可实现、可测试的详细设计。`doc/proposal.md` 开头误粘贴的三行 diff 摘要不参与本设计解释。本文固定字段名、模块边界、数据 Owner、接口契约、切块与检索算法、兼容策略和验证路径，不包含实际代码、迁移脚本或测试实现。

### 1.1 可验证目标

- BGE-M3 嵌入模型可以显式开启模型稀疏向量输出，非 BGE-M3 模型不能开启。
- Parent-Child 是现有六种分块策略之上的独立开关，开启后子块负责召回，父块负责 LLM 上下文。
- 知识库默认参数、文件任务级参数和单次查询参数有稳定字段、默认值、覆盖优先级和后端校验。
- PostgreSQL 保存父块完整文本、子块定位、文档版本和父子映射；Milvus 只保存子块和向量。
- Parent-Child child collection 逻辑上统一，物理上按向量维度拆分为 `rag_child_chunk_<dimension>`。
- 查询按知识库当前配置选择单一路径；不在一次查询里同时查旧单层 collection 与 Parent-Child child collection。
- 稠密/模型稀疏向量加权聚合、RRF 和 Rerank 的执行顺序固定且可测试。
- 旧知识库、旧文档、旧单层 chunk、旧 Milvus collection 和 LITE 模式保持兼容。

### 1.2 非目标

- 不实现 ColBERT，不新增 `colbert_vector`。
- 不扩展 BGE-M3 之外模型的稀疏向量输出。
- 不自动迁移、重切、重建存量文档。
- 不把 Parent-Child 添加为新的分块策略下拉项。
- 不把 Redis 作为父块、子块、检索结果或会话的最终事实来源。
- 不修改 AgentRun、FIFO、SSE、LangGraph checkpoint 或工作区路径边界。

## 2. 模块划分

本功能按下列模块实现。模块之间通过显式数据结构和 repository/service 边界连接，便于独立测试。

| 模块 | 语义 Owner | 职责 | 可独立测试方式 |
|---|---|---|---|
| 模型能力与配置规范化 | `yuxi.knowledge.chunking`、`yuxi.knowledge.base`、`yuxi.models.providers.cache` | 识别 BGE-M3、校验 Parent-Child 和向量融合参数、合并默认与覆盖参数 | unit |
| HTTP API 契约 | `server/routers/knowledge_router.py` | 接收创建、更新、入库、重切、查询参数，调用 service/manager | API integration |
| PostgreSQL 父子块存储 | `storage/postgres/models_knowledge.py`、新增 repository | 保存文档版本、父块、子块、span、图谱状态 | PostgreSQL integration |
| Milvus child collection | `knowledge/implementations/milvus.py` | 按维度管理 child collection，写入子块 dense/sparse vector 和 BM25 字段 | Milvus integration |
| Parent-Child 切块 | `knowledge/chunking/ragflow_like` | 复用六种底层策略生成父块，再在父块内生成子块和 span | unit + 入库 integration |
| 入库与显式重切 | `knowledge/base.py`、`knowledge/manager.py`、`services/task_service.py` | 按文件版本写入 PostgreSQL/Milvus，失败时保留旧有效版本 | integration / E2E |
| 检索编排 | `knowledge/implementations/milvus.py` | 单一路径选择、向量内聚合、RRF、Rerank、父块聚合 | unit + deterministic integration |
| 图谱映射 | `knowledge/graphs/*` | Parent-Child 下父块为语义主体、子块为下属节点 | integration |
| Redis 缓存 | `storage/redis` + 检索/父块读取服务 | 仅缓存可丢失数据，按 TTL 与删除/重切失效 | unit + integration |
| 前端交互 | `web/src/components`、`web/src/utils`、`web/src/apis` | 条件展示、参数输入、摘要、结果展示 | web unit + build + 页面检查 |

## 3. 配置模型

### 3.1 字段位置

知识库级创建和更新配置继续保存在 `knowledge_bases.additional_params`。文件入库或重切时实际采用的处理配置保存在 `knowledge_files.processing_params`。检索持久配置继续保存在 `knowledge_bases.query_params.options`。单次查询参数只通过查询请求 `meta` 临时覆盖，不写回数据库。

### 3.2 知识库级 `additional_params`

新增字段使用嵌套对象，避免与现有 `chunk_parser_config`、`stats`、连接器参数混淆。

```json
{
  "chunk_preset_id": "general",
  "chunk_parser_config": {},
  "embedding_features": {
    "bge_m3_sparse_enabled": false
  },
  "parent_child": {
    "enabled": false,
    "parent_token_num": 1000,
    "child_token_num": 200,
    "child_overlap_percent": 15,
    "separator": "\\n",
    "tokenizer_policy": "embedding_provider_or_tiktoken",
    "text_preservation": "parsed_markdown_raw"
  }
}
```

`embedding_features.bge_m3_sparse_enabled` 只对模型 ID 为 `Pro/BAAI/bge-m3` 或 `BAAI/bge-m3` 的 embedding 生效。模型 spec 可以带供应商前缀，能力判断使用最后一个冒号之后的模型 ID。示例：`siliconflow-cn:Pro/BAAI/bge-m3` 与 `Pro/BAAI/bge-m3` 等价。

### 3.3 文件级 `processing_params`

文件任务级参数沿用相同字段结构。入库前通过 `resolve_processing_params` 合并知识库默认、文件已有参数与本次请求参数，并把最终生效值写入 `knowledge_files.processing_params`。

```json
{
  "chunk_preset_id": "general",
  "chunk_parser_config": {},
  "chunk_engine_version": "ragflow_like_v1",
  "embedding_features": {
    "bge_m3_sparse_enabled": true
  },
  "parent_child": {
    "enabled": true,
    "parent_token_num": 1000,
    "child_token_num": 200,
    "child_overlap_percent": 15,
    "separator": "\\n",
    "tokenizer_id": "provider:<provider_id>:<model_id>",
    "text_preservation": "parsed_markdown_raw"
  },
  "indexing_path": "parent_child",
  "document_version_id": "docver_<id>"
}
```

`indexing_path` 固定为 `single_chunk` 或 `parent_child`，用于查询和兼容判断。`document_version_id` 指向本次成功写入的有效文档切片版本。

### 3.4 检索 `query_params.options`

新增检索字段如下。

| 字段 | 类型 | 默认值 | 限制 | 生效条件 |
|---|---|---:|---|---|
| `top_k_child` | integer | `30` | `1..500` | Parent-Child |
| `top_k_parent` | integer | `10` | `1..100`，且 `top_k_child >= top_k_parent` | Parent-Child |
| `use_vector_score_fusion` | boolean | `false` | 仅控制稠密 + BGE-M3 稀疏向量 | 稀疏向量已启用且模式为 vector/hybrid |
| `dense_vector_weight` | number | `0.7` | `0.0..5.0` | `use_vector_score_fusion=true` |
| `sparse_vector_weight` | number | `0.3` | `0.0..5.0` | `use_vector_score_fusion=true` |
| `use_rrf` | boolean | `false` | RRF 常数固定 `60` | 按显示条件生效 |

现有 `final_top_k` 在非 Parent-Child 模式下继续表示最终 chunk 数。Parent-Child 模式下 `top_k_parent` 是最终返回父块数量，`final_top_k` 仅作为旧调用方缺少 `top_k_parent` 时的兼容读取来源。

### 3.5 参数校验

配置校验由后端执行，前端校验只用于输入体验。

- `parent_child.enabled` 缺失时按 `false` 处理。
- `parent_token_num` 必须是整数，范围 `256..4096`。
- `child_token_num` 必须是整数，范围 `64..1024`。
- `parent_token_num > child_token_num`。
- `child_overlap_percent` 必须是整数或可无损转换为整数的数字，范围 `0..99`；`100` 直接拒绝。
- `separator` 必须是非空字符串，默认 `"\\n"`。
- 非 BGE-M3 模型携带 `embedding_features.bge_m3_sparse_enabled=true` 时后端拒绝。
- `dense_vector_weight` 与 `sparse_vector_weight` 原始值范围均为 `0.0..5.0`，两者不能同时为 `0`。
- 查询参数不满足显示条件时，后端忽略未生效字段；如果字段本身非法，后端拒绝，避免保存不可解释配置。

## 4. API 契约

### 4.1 创建知识库

入口继续使用 `POST /api/knowledge/databases`。请求体新增字段位于 `additional_params`。

```json
{
  "database_name": "产品资料库",
  "description": "...",
  "kb_type": "milvus",
  "embedding_model_spec": "siliconflow-cn:BAAI/bge-m3",
  "additional_params": {
    "chunk_preset_id": "general",
    "embedding_features": { "bge_m3_sparse_enabled": true },
    "parent_child": {
      "enabled": true,
      "parent_token_num": 1000,
      "child_token_num": 200,
      "child_overlap_percent": 15,
      "separator": "\\n"
    }
  },
  "share_config": { "version": 2 }
}
```

`KnowledgeBaseManager.create_database` 获取 executor 后调用 `normalize_additional_params`。Milvus executor 在该阶段校验 embedding 模型能力、Parent-Child 参数和稀疏向量配置。创建响应通过 `serialize_knowledge_base` 返回规范化后的 `additional_params`，用于前端确认页和详情页回读。

### 4.2 更新知识库配置

入口继续使用 `PUT /api/knowledge/databases/{kb_id}`。`additional_params.parent_child` 的更新只影响未来新入库或显式重切文件，不触发后台重建。更新时对当前知识库 embedding 模型重新校验稀疏向量能力。旧文件的 `processing_params.indexing_path` 和 `document_version_id` 不被修改。

### 4.3 添加与入库文档

以下入口继续接收 `params`：

- `POST /api/knowledge/databases/{kb_id}/documents`
- `POST /api/knowledge/databases/{kb_id}/documents/index`
- `POST /api/knowledge/databases/{kb_id}/documents/index-pending`

`params.parent_child` 和 `params.embedding_features` 覆盖知识库默认值，只影响本次任务。`add_documents` 当前只提取 `chunk_preset_id` 和 `chunk_parser_config` 作为自动入库参数，后续实现需要把 `parent_child` 与 `embedding_features` 同步纳入 `indexing_params`，再由 `knowledge_base.update_file_params` 保存最终生效配置。

### 4.4 显式重切片

新增入口：

```text
POST /api/knowledge/databases/{kb_id}/documents/reslice
GET  /api/knowledge/databases/{kb_id}/documents/reslice/{task_id}/events
```

请求体：

```json
{
  "file_ids": ["file_xxx"],
  "params": {
    "chunk_preset_id": "general",
    "parent_child": { "enabled": true },
    "embedding_features": { "bge_m3_sparse_enabled": true }
  }
}
```

`reslice` 使用 `tasker` 创建异步任务，任务类型为 `knowledge_reslice`。同一 `kb_id + file_ids + params fingerprint` 可以排队多个显式任务；同一文件正在重切时，后端拒绝新的同文件重切请求。SSE 事件只暴露任务快照，不包含敏感 payload。

重切片和普通入库共用底层切块、写入和验证模块，但重切片使用版本化提交：新版本全部写入并回读成功后才切换文件有效版本。

### 4.5 查询参数配置

入口继续使用：

```text
GET /api/knowledge/databases/{kb_id}/query-params
PUT /api/knowledge/databases/{kb_id}/query-params
```

`get_query_params_config` 根据知识库 `additional_params` 和当前查询配置动态返回可见配置项。返回项支持现有 `depend_on`，同时新增 `visible_when` 表达复杂条件。

```json
{
  "key": "use_rrf",
  "label": "RRF 倒排融合",
  "type": "boolean",
  "default": false,
  "visible_when": {
    "any": [
      { "search_mode": "hybrid" },
      { "use_graph_retrieval": true }
    ]
  }
}
```

前端旧版只理解 `depend_on`。实现新增 `visible_when` 时，`SearchConfigPanel.vue` 同步支持；后端仍是最终校验边界。

### 4.6 查询与返回结果

查询入口继续使用 `POST /api/knowledge/databases/{kb_id}/query` 和 `query-test`。

Parent-Child 返回项保持旧结果字段兼容，同时在 `metadata.parent_child` 中增加专属字段。

```json
{
  "id": "parent_<uuid>",
  "content": "完整父块文本",
  "score": 0.91,
  "metadata": {
    "result_type": "parent_child_parent",
    "kb_id": "kb_xxx",
    "doc_id": "doc_<uuid>",
    "file_id": "file_xxx",
    "parent_id": "parent_<uuid>",
    "parent_score": 0.91,
    "fusion_score": 0.88,
    "rerank_score": 0.91,
    "child_hits": [
      {
        "child_id": "child_<uuid>",
        "score": 0.88,
        "dense_score": 0.72,
        "sparse_score": 0.65,
        "rrf_score": 0.0325,
        "rerank_score": 0.91,
        "chunk_index": 2,
        "spans": [
          { "start_offset": 120, "end_offset": 240, "page_num": 1 }
        ],
        "metadata": { "source": "file.pdf" }
      }
    ]
  }
}
```

非 Parent-Child 查询继续返回旧 chunk 结构，不要求调用方读取 `metadata.parent_child`。

## 5. PostgreSQL 设计

### 5.1 表结构

新增表与现有 `knowledge_chunks` 并存，不替代旧表。

```text
knowledge_document_versions
  version_id VARCHAR(64) PRIMARY KEY
  kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE
  file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE
  doc_id VARCHAR(64) NOT NULL
  indexing_path VARCHAR(32) NOT NULL
  embedding_model_spec VARCHAR(512) NOT NULL
  embedding_dimension INTEGER NOT NULL
  chunk_preset_id VARCHAR(32) NOT NULL
  processing_params JSONB NOT NULL
  status VARCHAR(32) NOT NULL
  activated_at TIMESTAMPTZ NULL
  created_at TIMESTAMPTZ NOT NULL
  updated_at TIMESTAMPTZ NOT NULL

knowledge_parent_chunks
  parent_id VARCHAR(64) PRIMARY KEY
  version_id VARCHAR(64) NOT NULL REFERENCES knowledge_document_versions(version_id) ON DELETE CASCADE
  kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE
  file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE
  doc_id VARCHAR(64) NOT NULL
  parent_index INTEGER NOT NULL
  parent_text TEXT NOT NULL
  start_offset INTEGER NOT NULL
  end_offset INTEGER NOT NULL
  token_count INTEGER NOT NULL
  metadata JSONB NOT NULL
  graph_structure_indexed BOOLEAN NOT NULL DEFAULT FALSE
  graph_indexed BOOLEAN NOT NULL DEFAULT FALSE
  graph_extraction_details JSONB NOT NULL
  ent_ids JSONB NULL
  tags JSONB NULL
  extraction_result JSONB NULL
  created_at TIMESTAMPTZ NOT NULL
  updated_at TIMESTAMPTZ NOT NULL

knowledge_child_chunks
  child_id VARCHAR(64) PRIMARY KEY
  parent_id VARCHAR(64) NOT NULL REFERENCES knowledge_parent_chunks(parent_id) ON DELETE CASCADE
  version_id VARCHAR(64) NOT NULL REFERENCES knowledge_document_versions(version_id) ON DELETE CASCADE
  kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE
  file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE
  doc_id VARCHAR(64) NOT NULL
  child_index INTEGER NOT NULL
  child_text TEXT NOT NULL
  start_offset INTEGER NOT NULL
  end_offset INTEGER NOT NULL
  token_count INTEGER NOT NULL
  spans JSONB NOT NULL
  metadata JSONB NOT NULL
  created_at TIMESTAMPTZ NOT NULL
  updated_at TIMESTAMPTZ NOT NULL
```

`doc_id` 是独立文档标识，创建文件记录时生成并保存在 `knowledge_files.processing_params.doc_id`。旧文件没有 `doc_id` 时，在首次 Parent-Child 入库或显式重切时生成；它不等同于 `file_id`。

### 5.2 约束与索引

- `knowledge_document_versions` 对 `(kb_id, file_id, status)` 建索引，用于查找有效版本和清理失败版本。
- `knowledge_document_versions` 对 `(kb_id, file_id)` 允许多个版本，但同一文件最多一个 `status='active'` 版本。PostgreSQL 用部分唯一索引实现：`WHERE status = 'active'`。
- `knowledge_parent_chunks` 对 `(version_id, parent_index)` 建唯一约束。
- `knowledge_child_chunks` 对 `(version_id, child_index)` 建唯一约束。
- `knowledge_child_chunks` 对 `parent_id`、`file_id`、`kb_id` 建索引。
- `spans` 使用 JSONB 数组保存，不在 v1 设计 GIN 查询；定位由读取结果直接使用。
- 删除 `knowledge_files` 或 `knowledge_bases` 时，版本、父块、子块通过外键级联删除。

### 5.3 Repository

新增 `KnowledgeParentChildChunkRepository`，职责如下：

- `create_staging_version(kb_id, file_id, doc_id, params, embedding_model_spec, embedding_dimension)`：创建 `status='staging'` 的版本。
- `batch_insert_parent_chunks(version_id, parents)`：批量写入父块。
- `batch_insert_child_chunks(version_id, children)`：批量写入子块。
- `get_active_version(kb_id, file_id)`：读取当前有效版本。
- `activate_version(version_id)`：在同一事务中把旧 active 改为 superseded，把目标 staging 改为 active，并更新 `knowledge_files.processing_params.document_version_id/indexing_path`。
- `delete_version(version_id)`：删除 staging 或 superseded 版本及父子记录。
- `list_children_by_ids(child_ids)`：按输入顺序回读子块。
- `list_parents_by_ids(parent_ids)`：按输入顺序回读父块。
- `delete_active_by_file_id(kb_id, file_id)`：文档删除或重切清理时显式删除父子数据。

Repository 不接触 Milvus SDK，不拼装 HTTP 响应，不持有宿主路径。

## 6. Milvus 设计

### 6.1 Collection 命名

Parent-Child child collection 逻辑名为 `rag_child_chunk`，物理 collection 按 embedding 维度拆分：

```text
rag_child_chunk_1024
rag_child_chunk_768
rag_child_chunk_<dimension>
```

同一个知识库下所有文档必须使用同一个 embedding 模型和同一个向量维度。创建知识库后不允许在该知识库内变更 `embedding_model_spec`。如果未来支持模型变更，必须作为显式重建能力另行设计。

### 6.2 Child collection schema

```text
id                VARCHAR primary key，等于 child_id
knowledge_base_id VARCHAR
file_id           VARCHAR
doc_id            VARCHAR
version_id        VARCHAR
child_id          VARCHAR
parent_id         VARCHAR
child_text        VARCHAR enable_analyzer=true
chunk_index       INT64
meta_info         JSON
dense_vector      FLOAT_VECTOR(dim=<dimension>)
bge_m3_sparse_vector SPARSE_FLOAT_VECTOR，可选写入
content_sparse    SPARSE_FLOAT_VECTOR，由 Milvus BM25 function 生成
```

`content_sparse` 继续表示 Milvus 内置 BM25 字段。`bge_m3_sparse_vector` 表示 BGE-M3 模型输出的稀疏向量，两者不能复用字段名或检索参数。

### 6.3 索引

- `dense_vector` 使用 `IVF_FLAT`，`metric_type='IP'`。
- `bge_m3_sparse_vector` 使用 `SPARSE_INVERTED_INDEX`，`metric_type='IP'`。
- `content_sparse` 使用 Milvus BM25 function 和 `SPARSE_INVERTED_INDEX`，`metric_type='BM25'`。
- `nlist` 默认沿用现有 `1024`，后续可在性能专项中按数据规模调整；本功能不新增用户可见 `nlist` 配置。

### 6.4 写入与删除

Parent-Child 模式写入只把子块写入 Milvus。父块不写入 Milvus。Milvus 写入表达式必须包含 `knowledge_base_id == kb_id` 过滤，防止全局 collection 跨知识库串读。

删除文档时按 `knowledge_base_id` 与 `file_id` 删除对应子块。删除知识库时不能 drop 全局 child collection，只删除该知识库的子块记录；旧单层 collection 仍按现有 `kb_id` collection 清理。

### 6.5 Collection 选择

查询时先读取知识库配置和目标文件有效版本，选择单一路径：

- 知识库当前 `parent_child.enabled=false`：只查询旧单层 `kb_id` collection 和 `knowledge_chunks`。
- 知识库当前 `parent_child.enabled=true`：只查询 `rag_child_chunk_<dimension>` 和父子表。

同一知识库存在旧单层文档和 Parent-Child 文档时，查询按知识库当前配置选择单一路径，忽略另一类文档。系统不在一次查询里合并两类数据。

## 7. Parent-Child 切块设计

### 7.1 Tokenizer

Parent-Child 切块使用 tokenizer registry 获取 tokenizer：

1. 优先使用 embedding provider 返回或声明的 tokenizer，要求与 embedding 模型计数一致。
2. provider 未声明 tokenizer 时，使用本地 `tiktoken` 统一适配。
3. provider tokenizer 与 `tiktoken` 均不可用时，拒绝入库或重切，文件状态进入 `error_indexing`，错误信息包含 tokenizer 不可用原因。

非 Parent-Child 的既有场景继续使用当前统一 tokenizer，不因本功能改变。

### 7.2 文本保留

父块和子块都基于解析后的 Markdown 字符串。Parent-Child 不对 Markdown、空白、换行或标点做额外规范化。`parent_text` 保存父块覆盖范围内的原文切片，`child_text` 保存父块范围内的原文切片。`start_offset` 和 `end_offset` 使用 Python 字符索引。

现有六种策略内部如果产生规范化结果，Parent-Child 包装层只使用它们提供的切分边界，不使用规范化后的文本作为最终存储文本。实现需要为各策略补齐“边界输出”适配；无法提供边界时，该策略在 Parent-Child 模式下拒绝入库并返回可诊断错误，不能退回到规范化文本。

### 7.3 父块生成

Parent-Child 先使用用户选择的 `chunk_preset_id` 形成候选边界，再按 `parent_token_num` 合并或截断为父块。父块可以跨页、跨段落。父块不得为空，token 数不得超过 `parent_token_num`。单个原始片段超过父块上限时，使用 tokenizer 对原文硬切，并保留字符偏移。

父块 ID 使用确定性输入生成：

```text
parent_id = "parent_" + hash(kb_id, file_id, version_id, parent_index, start_offset, end_offset)
```

### 7.4 子块生成

每个父块内部独立生成子块。子块不得跨出父块范围，token 数不得超过 `child_token_num`。重叠 token 数为：

```text
overlap_tokens = floor(child_token_num * child_overlap_percent / 100)
step_tokens = child_token_num - overlap_tokens
```

由于 `child_overlap_percent=100` 已被拒绝，`step_tokens` 必须大于 `0`。子块 ID 使用确定性输入生成：

```text
child_id = "child_" + hash(kb_id, file_id, version_id, parent_id, child_index, start_offset, end_offset)
```

### 7.5 Span 列表

每个子块保存 `spans`：

```json
[
  { "start_offset": 120, "end_offset": 240, "page_num": 1 },
  { "start_offset": 240, "end_offset": 310, "page_num": 2 }
]
```

`spans` 记录子块覆盖的全部原始文档字符区间和页码。跨页子块生成多个 span。重叠子块各自保存独立 span，允许不同子块 span 交叠。`start_offset/end_offset` 仍为解析后 Markdown 的 Python 字符索引；`page_num` 来自 parser 输出的页码映射。parser 无法提供页码时，`page_num` 为 `null`，但 span 仍必须保存字符区间。

## 8. 入库与显式重切设计

### 8.1 普通新文档入库

入库流程沿用当前 `Tasker` 与 `knowledge_base.index_file`：

1. 读取知识库配置和文件 `processing_params`。
2. 合并请求覆盖参数，得到最终处理参数并写回文件记录。
3. 根据 `parent_child.enabled` 选择 `single_chunk` 或 `parent_child` 路径。
4. `single_chunk` 路径沿用现有 `knowledge_chunks + kb_id collection`。
5. `parent_child` 路径创建 staging document version。
6. 读取解析后的 Markdown。
7. 解析页码 span 映射并执行 Parent-Child 切块。
8. 写入 PostgreSQL 父块、子块。
9. 生成 dense embedding；BGE-M3 且开启稀疏向量时同时生成 `bge_m3_sparse_vector`。
10. 写入物理 child collection。
11. 回读 PostgreSQL 父子数量、Milvus 子块数量和关键字段。
12. 激活版本，更新文件 `status=indexed`、`chunk_count`、`token_count`、`processing_params.document_version_id`。

步骤 8 到 11 任一关键写入失败时，删除 staging 版本和已写入的 Milvus 子块，文件状态进入 `error_indexing`。Redis 缓存失败不影响核心入库成功，但必须记录结构化 warning。

### 8.2 显式重切

显式重切和新入库共用 Parent-Child 写入模块，但 activation 前不删除旧 active 版本。新版本验证成功后，在一个 PostgreSQL 事务内切换 active 版本，再清理旧父子表、旧 Milvus 子块和 Redis 缓存。

失败语义固定为：新版本任一步失败时，不切换 active 版本，旧数据继续提供检索；任务结果标记 `failed` 并保留错误原因、目标文件、已完成阶段和可重试信息。

### 8.3 文件统计

Parent-Child 模式下 `knowledge_files.chunk_count` 记录子块数量，`token_count` 记录父块 token 总和。详情页需要同时展示父块数量时，从 `knowledge_document_versions` 或 repository 聚合读取，不复用旧 `chunk_count` 表达两个含义。

## 9. 检索设计

### 9.1 单一路径选择

`MilvusKB.aquery` 读取 `config.additional_params.parent_child.enabled` 后选择一个检索 executor：

- `SingleChunkRetrievalExecutor`：封装现有单层 chunk 查询逻辑。
- `ParentChildRetrievalExecutor`：封装 child collection 查询、融合、rerank 和父块聚合。

选择发生在任何 Milvus 查询之前。查询不会先尝试一种路径失败后静默切换到另一种路径。

### 9.2 向量内聚合

当 BGE-M3 稀疏向量已启用且 `use_vector_score_fusion=true` 时，向量一路由 dense 与 BGE-M3 sparse 两个候选列表融合。

每一路在本次查询候选集内独立 Min-Max 归一化：

```text
if candidates is empty: 不参与融合
if max_score == min_score: 所有候选归一化为 1.0
else normalized = (score - min_score) / (max_score - min_score)
```

权重归一化：

```text
sum_w = dense_vector_weight + sparse_vector_weight
dense_w_norm = dense_vector_weight / sum_w
sparse_w_norm = sparse_vector_weight / sum_w
```

候选身份在单层模式下为 `chunk_id`，在 Parent-Child 模式下为 `child_id`。只命中一路的候选，缺失一路归一化分数按 `0` 处理。

```text
vector_score = dense_w_norm * dense_score_normalized
             + sparse_w_norm * sparse_score_normalized
```

`use_vector_score_fusion=false` 时，向量一路只使用 dense 召回结果。该聚合不融合 BM25 或图检索分数。

### 9.3 RRF

`use_rrf=true` 时，RRF 在 Rerank 之前执行。RRF 只融合三类列表：

- 向量一路结果：dense 或 dense+sparse 聚合后的结果。
- BM25 结果。
- 图检索结果。

候选身份在单层模式下为 `chunk_id`，在 Parent-Child 模式下为 `child_id`。公式固定为：

```text
rrf_score = Σ 1 / (60 + rank)
```

`rank` 从 `1` 开始。缺失或关闭的召回分支不参与求和。RRF 输出统一候选列表；Rerank 只能接收该列表，不能接收各路原始列表。

### 9.4 非 Parent-Child 查询

非 Parent-Child 模式保持现有 `chunk_id/content/file_id/chunk_index` 返回结构。新增逻辑只在开关满足条件时介入：

- 稀疏向量关闭且 RRF 关闭：保留升级前 dense、BM25、hybrid 和图检索内部融合行为。
- 稀疏向量开启但 RRF 关闭：先做 dense+sparse 向量内聚合，再与 BM25 按现有 `vector_weight/bm25_weight` 逻辑融合。
- RRF 开启：向量一路、BM25 和图检索进入全链路 RRF。

### 9.5 Parent-Child 查询

Parent-Child 查询流程：

1. 从 `rag_child_chunk_<dimension>` 查询 `top_k_child` 个子块候选，并始终带 `knowledge_base_id == kb_id` 过滤。
2. 按配置执行 dense/sparse 向量内聚合。
3. 按配置执行 BM25 和图检索。
4. 如果启用 RRF，以 `child_id` 为候选身份执行 RRF。
5. 如果启用 Rerank，用子块文本作为 rerank 文档。
6. RRF/Rerank 后按 `parent_id` 聚合。
7. 同一父块命中多个子块时，父块分数取最高子块分数。
8. 按父块分数排序，截取 `top_k_parent` 个父块。
9. 批量读取 `parent_text`。
10. 返回父块文本作为 `content`，并在 metadata 中保留命中子块列表、span、页码和来源元数据。

传给 LLM 的上下文只包含去重后的完整父块文本。子块文本只用于召回、RRF、Rerank 和来源定位。

## 10. 图谱设计

Parent-Child 模式下父块是图谱语义主体，子块是父块下属节点。Neo4j 写入形态为：

```text
(ParentChunk {parent_id}) -[:HAS_CHILD]-> (ChildChunk {child_id})
(ParentChunk)-[:MENTIONS]->(Entity)
(Triple)-[:MENTIONED_IN]->(ParentChunk)
```

图检索需要输出可映射回 `child_id` 的结果。父块图谱扩散得到父块候选后，repository 读取该父块下属子块，并按父块图谱分数分配给子块候选，使后续 RRF 仍以 `child_id` 统一身份融合。

历史 `knowledge_chunks` 图谱关系保持不变。旧单层查询仍使用旧 `chunk_id` 图谱路径。Parent-Child 查询不读取旧 `knowledge_chunks` 图谱关系，除非显式兼容映射器把旧 chunk 转为当前路径；该映射器不在 v1 查询默认路径中启用。

## 11. Redis 缓存设计

Redis key 必须包含知识库、版本和对象身份，避免配置切换后误读。

| 缓存 | Key | TTL | 失效 |
|---|---|---:|---|
| 父块文本 | `kb:{kb_id}:pc:v:{version_id}:parent:{parent_id}` | `3600s` | 文档删除、版本清理、显式重切成功后删除 |
| 检索结果 | `kb:{kb_id}:pc:v:{version_id}:query:{fingerprint}` | `600s` | 文档删除、版本清理、显式重切成功后删除 |
| 会话上下文 | `kb:{kb_id}:rag-session:{session_id}` | `86400s` | TTL 自动过期 |

缓存 miss、Redis 不可用或缓存删除失败不改变 PostgreSQL/Milvus 的事实状态。核心查询必须回源 PostgreSQL 或重新检索。Redis 异常记录结构化日志并暴露 degraded 信息，不能返回看似成功但实际未执行的空结果。

## 12. 前端设计

### 12.1 创建知识库

`DatabaseCreateFlowModal.vue` 在嵌入模型选择后使用前端工具函数 `isBgeM3EmbeddingModelSpec` 判断是否显示稀疏向量开关。切换到非 BGE-M3 时，前端清除 `embedding_features.bge_m3_sparse_enabled` 或置为 `false`。

Parent-Child 开关显示在分块策略下方。开启后展示父块 Token 数、子块 Token 数、子块重叠比例和分隔符。第 3 步摘要展示：

```text
父子层级分块：已启用 / 未启用
稀疏向量输出：已启用 / 未启用
```

### 12.2 入库参数

入库对话框或相关参数面板复用同一组 Parent-Child 表单组件。字段默认继承知识库级参数；用户修改后作为任务级 `params.parent_child` 提交。关闭开关时不提交对该任务生效的 Parent-Child 参数，或提交 `enabled=false`。

### 12.3 详情与检索配置

`DataBaseInfoView.vue` 显示知识库当前 Parent-Child 状态和参数。保存知识库级参数只更新默认值，不触发旧文件重切。

`SearchConfigPanel.vue` 支持 `visible_when`。向量加权聚合与权重只在后端返回配置项时显示。前端保存原始权重值，不把 `0.7/0.5` 归一化为 `0.583/0.417`。

### 12.4 检索结果展示

检索结果组件根据 `metadata.result_type` 区分旧 chunk 与 Parent-Child 父块结果。Parent-Child 结果展示父块文本，并把命中子块作为来源定位信息展示。高亮使用 `child_hits[].spans`，跨页时展示多个页码或定位段。

## 13. 权限、LITE 与失败语义

- 创建、更新知识库配置、入库、显式重切继续使用现有管理权限。
- 查询、父块读取、子块定位读取继续使用现有读权限和知识库可见性查询。
- 后端能力校验不能依赖前端隐藏控件。
- LITE 模式不注册 Milvus、图谱、Parent-Child child collection、BGE-M3 稀疏向量推理或知识库路由。能力发现不得触发这些模块初始化。
- PostgreSQL/Milvus 核心写入失败时，文件或任务状态必须失败；Redis 失败按缓存降级处理。
- 查询结果必须来自本次命中的 `child_id/parent_id/version_id`，不能从相邻查询、相邻文档或旧版本猜测父块文本。

## 14. 测试与证据设计

### 14.1 Unit

- BGE-M3 spec 匹配：无前缀、有前缀、非 BGE-M3。
- Parent-Child 参数整数、范围、父大于子、`100%` 重叠拒绝。
- 知识库默认、文件已有参数、任务级参数合并优先级。
- tokenizer provider、tiktoken、不可用拒绝路径。
- 原始 Markdown 文本切片与 Python 字符 offset。
- span 跨页与重叠子块独立保存。
- Min-Max 空集合、单值集合、全同分集合。
- 权重归一化、单路为零、双零拒绝。
- RRF 排名、重复候选合并、缺失召回分支。
- Parent-Child `parent_id` 去重、最高子块分数和 `top_k_child/top_k_parent` 截取。

### 14.2 PostgreSQL integration

- 文档版本、父块、子块真实写入与回读。
- active 版本部分唯一约束。
- 重切成功后旧版本 superseded 或清理，失败后旧 active 保留。
- 删除文件和删除知识库级联清理父子记录。
- 旧 `knowledge_chunks` 在无新字段时继续读取。

### 14.3 API integration

- 创建知识库保存 `embedding_features` 和 `parent_child` 并从详情接口读回。
- 非 BGE-M3 携带稀疏配置返回参数错误。
- 文件入库任务级参数覆盖知识库默认值。
- 查询参数配置根据条件返回或隐藏向量加权聚合/RRF。
- 无管理权限不能更新配置或触发重切。

### 14.4 Milvus / 检索 integration 或 E2E

- 按维度创建 `rag_child_chunk_<dimension>`。
- 同一知识库不允许混用不同 embedding 模型或维度。
- Parent-Child 只把子块写入 Milvus，父块只在 PostgreSQL。
- 子块 collection 字段和索引类型符合设计。
- Parent-Child 查询按 `knowledge_base_id` 过滤，不跨知识库串读。
- 混合旧单层和新父子文档时，查询只走知识库当前配置路径并忽略另一类文档。
- RRF 在 Rerank 前执行，Rerank 输入为统一候选列表。
- 返回父块文本并保留子块 span。

### 14.5 Web unit、build 与页面检查

- 创建页 BGE-M3 条件展示、切换非 BGE-M3 清除稀疏配置。
- Parent-Child 开关、参数校验和确认页摘要。
- 入库参数继承与覆盖展示。
- 查询配置 `visible_when` 生效。
- 原始权重保存后仍按原始值展示。
- Parent-Child 结果显示父块和命中子块定位；旧 chunk 结果兼容。

## 15. 实施顺序

1. 新增配置规范化与 unit 测试。
2. 新增 PostgreSQL 模型、迁移和 repository，并完成 integration 测试。
3. 新增 tokenizer registry 与 Parent-Child 切块模块，并完成 span/offset unit 测试。
4. 扩展 Milvus child collection 管理和写入路径。
5. 接入新文档 Parent-Child 入库与显式重切任务。
6. 接入 Parent-Child 查询、向量内聚合、RRF 和 Rerank 顺序。
7. 接入图谱 Parent/Child 映射。
8. 接入 Redis 缓存与失效。
9. 更新前端创建、详情、入库、查询配置和结果展示。
10. 补齐 integration/E2E、LITE 验证、旧数据兼容验证和独立 Review。

每一步只修改对应模块和必要装配点。未完成后续步骤时，已完成模块不得对用户宣称整体功能可用。

## 16. 验收矩阵

| 验收主张 | 失败面 | 语义 Owner | 直接证据 | 负向案例 |
|---|---|---|---|---|
| 只有 BGE-M3 可开启模型稀疏向量 | 非 BGE-M3 被保存为启用 | 配置规范化、创建 API | unit + API integration + DB 回读 | 非 BGE-M3 携带 true 被拒绝 |
| Parent-Child 是独立开关并复用六种策略 | 下拉新增策略或关闭后仍走父子链路 | chunking dispatcher、前端创建表单 | web unit + chunking unit | 关闭 Parent-Child 只生成旧单层 chunk |
| 父子参数边界稳定 | 小数、越界、100% 重叠被接受 | 参数校验模块 | unit + API integration | `child_overlap_percent=100` 返回参数错误 |
| tokenizer 不可用时拒绝入库 | 静默降级导致 token 计数不一致 | tokenizer registry、入库 service | unit + 入库 integration | provider/tiktoken 都不可用时文件 `error_indexing` |
| 父块只在 PostgreSQL，子块在 Milvus | 父块进入 Milvus 或子块无法回父块 | PG repository、Milvus executor | integration + 数据回读 | Milvus 存在父块记录或缺 `parent_id` |
| 查询只走当前配置单一路径 | 同时查旧单层和父子路径 | Milvus 查询编排 | deterministic integration | 混合数据查询返回另一类路径结果 |
| dense/sparse 聚合按候选集归一化 | 未归一化或混入 BM25/图分数 | 检索 scorer | unit + retrieval integration | 双零权重被拒绝 |
| RRF 在 Rerank 前执行 | Rerank 接收多路原始列表 | 检索编排 | deterministic replay/E2E | Rerank 输入绕过 RRF 时测试失败 |
| Parent-Child 返回父块且保留子块定位 | 重复父块或丢 span | 父块 repository、返回 schema | E2E + 协议回读 | 同父多子命中仍只返回一个父块 |
| LITE 不加载知识重运行时 | LITE 初始化 Milvus/图谱/稀疏推理 | runtime composition | LITE readiness 检查 | LITE 能力发现触发知识模块初始化 |

## 17. 未验证范围

本文档为详细设计，新增行为均未实现、未迁移、未执行产品链路测试。当前仅通过只读源码检查确认现有 Owner 与装配点。后续实现必须按本设计补齐测试、迁移、真实 PostgreSQL/Milvus/HTTP/前端验证和独立 Reviewer 审查。
