# 知识库稀疏-稠密向量与 Parent-Child 检索需求提案

状态：需求提案
类型：feature
文档范围：本文件只定义产品需求、行为边界、兼容承诺和验收标准；详细设计、接口定稿、数据库迁移脚本和实现代码在后续阶段单独产出。

## 1. 背景与目标

Yuxi 当前的 Milvus 知识库支持文档解析、分块、稠密向量检索、Milvus 内置 BM25 检索、混合检索、图检索和重排序。知识库创建页已经提供嵌入模型与分块策略配置，知识库详情页已经提供分块策略编辑和检索参数配置。

本需求增加以下能力：

1. 对 BGE-M3 嵌入模型增加稀疏向量输出开关。
2. 在现有分块策略之上增加独立的 Parent-Child 双层切块开关。
3. 在知识库级别和单文件入库任务级别配置父块 Token 数、子块 Token 数、子块重叠比例和分隔符。
4. 在 Parent-Child 模式下使用子块召回、父块供给 LLM，并保留子块来源定位信息。
5. 为稠密向量和稀疏向量增加候选集 Min-Max 归一化后的加权聚合。
6. 在满足条件的检索模式下增加 Reciprocal Rank Fusion（RRF）开关，并在重排序之前完成多路召回融合。
7. 保持已有知识库、已有文档和旧向量数据的现有行为，不自动迁移、不自动重建。

## 2. 非目标

本提案不包含以下内容：

- 本次不修改前后端代码、数据库迁移、Milvus collection 或测试实现。
- 本次不实现 ColBERT，不创建或填充 `colbert_vector`。
- 本次不扩展 BGE-M3 之外的嵌入模型稀疏向量能力。
- 本次不自动对存量文档重新切块、重新生成向量或迁移到新 collection。
- 本次不改变现有 `General / QA / Book / Laws / Semantic / Separator` 下拉选项。
- 本次不把 Parent-Child 做成独立的分块策略选项。
- Redis 缓存不是本次必须落地的持久化行为；本提案只记录推荐的缓存方案。
- 详细的 API 字段命名、Pydantic 类型、数据库索引实现、Milvus SDK 调用细节和迁移执行顺序留给后续详细设计确认。

## 3. 已确认的现状与语义 Owner

以下现状已通过当前仓库源码和已有文档核对。它们用于定位后续实现边界，不代表本提案已经完成实现。

| 领域 | 当前 Owner | 当前事实 |
|---|---|---|
| 创建知识库 HTTP 入口 | [`backend/server/routers/knowledge_router.py`](../backend/server/routers/knowledge_router.py) | 创建请求接收嵌入模型、知识库类型、`additional_params` 和共享配置，并委托知识库 Manager。 |
| 知识库创建与配置持久化 | [`backend/package/yuxi/knowledge/manager.py`](../backend/package/yuxi/knowledge/manager.py) | 知识库配置最终写入 PostgreSQL；知识库级 `additional_params` 和查询参数分别保存。 |
| 分块策略注册与参数合并 | [`backend/package/yuxi/knowledge/chunking/ragflow_like/presets.py`](../backend/package/yuxi/knowledge/chunking/ragflow_like/presets.py) | 当前策略为 `General / QA / Book / Laws / Semantic / Separator`；知识库、文件和请求参数按既有优先级合并。 |
| 现有单层切块 | [`backend/package/yuxi/knowledge/chunking/ragflow_like/dispatcher.py`](../backend/package/yuxi/knowledge/chunking/ragflow_like/dispatcher.py) | 当前策略产出普通 chunk 记录，并计算可用的字符偏移。 |
| Milvus 文档向量存储与检索 | [`backend/package/yuxi/knowledge/implementations/milvus.py`](../backend/package/yuxi/knowledge/implementations/milvus.py) | 当前 collection 以知识库 ID 命名；已存在稠密向量字段和用于 BM25 的稀疏字段；当前查询支持向量、BM25、混合、图检索和重排序。 |
| PostgreSQL chunk 事实 | [`backend/package/yuxi/storage/postgres/models_knowledge.py`](../backend/package/yuxi/storage/postgres/models_knowledge.py) | 当前 `knowledge_chunks` 保存单层 chunk 正文、文件关系、chunk 序号、字符/Token 偏移和图谱处理字段。 |
| PostgreSQL chunk 访问 | [`backend/package/yuxi/repositories/knowledge_chunk_repository.py`](../backend/package/yuxi/repositories/knowledge_chunk_repository.py) | Repository 封装 chunk 查询、批量写入、按文件删除和图谱状态更新。 |
| 创建知识库前端流程 | [`web/src/components/knowledge/DatabaseCreateFlowModal.vue`](../web/src/components/knowledge/DatabaseCreateFlowModal.vue) | 当前创建流程分为类型、配置、权限三步；嵌入模型和分块策略在配置页选择，第三步展示摘要。 |
| 创建表单请求组装 | [`web/src/utils/databaseCreateForm.js`](../web/src/utils/databaseCreateForm.js) | 当前把分块策略和类型参数组装到创建请求的 `additional_params`。 |
| 知识库详情与检索配置 | [`web/src/views/DataBaseInfoView.vue`](../web/src/views/DataBaseInfoView.vue)、[`web/src/components/SearchConfigPanel.vue`](../web/src/components/SearchConfigPanel.vue) | 详情页提供分块策略编辑和检索配置面板；检索配置由后端动态下发参数定义。 |
| 知识库 API 封装 | [`web/src/apis/knowledge_api.js`](../web/src/apis/knowledge_api.js) | 前端集中调用知识库创建、文件入库、查询和查询参数接口。 |

当前系统边界仍以 [`ARCHITECTURE.md`](../ARCHITECTURE.md) 为准：PostgreSQL 保存业务事实，Redis 保存短期事件和缓存，Milvus 负责向量检索，Neo4j 负责可选图谱能力；LITE 模式不能在启动、路由注册或能力发现阶段加载知识库、图谱和稀疏向量重运行时。

## 4. 用户可见需求

### 4.1 创建知识库配置页

创建知识库的第 2/3 步在选择嵌入模型后，根据模型标识判断是否展示稀疏向量开关。

#### 稀疏向量输出开关

只有模型标识匹配以下两个模型 ID 时显示“是否启用稀疏向量输出”：

```text
Pro/BAAI/bge-m3
BAAI/bge-m3
```

模型标识可能带有供应商前缀，例如：

```text
siliconflow-cn:Pro/BAAI/bge-m3
siliconflow-cn:BAAI/bge-m3
```

供应商前缀不改变 BGE-M3 的匹配结果。除上述模型外，即使其他嵌入模型的后端未来具备稀疏向量能力，本阶段也不显示该选项。

切换到非 BGE-M3 模型时，界面不得继续展示稀疏向量开关，也不得提交一个对该模型生效的稀疏向量配置。后端仍必须校验模型能力，前端隐藏不是授权或能力校验边界。

#### Parent-Child 开关

在分块策略下方增加“启用父子层级分块 (Parent-Child)”复选框。

该控件是独立开关，不是分块策略下拉列表的新选项。分块策略下拉列表继续只包含：

```text
General / QA / Book / Laws / Semantic / Separator
```

开关启用后，以用户选择的分块策略作为底层算法执行双层切分：

- 先依据父块 Token 数形成父块。
- 再在每个父块范围内依据子块 Token 数、子块重叠比例和分隔符生成子块。
- 父块允许跨页、跨段落；一个父块可以关联多个子块。
- 父块保存切分时未删减的完整父文本，作为后续 LLM 上下文。
- 子块保存用于向量化和检索的文本片段；子块文本与父块中的对应片段保持一致，不做二次改写或删减。

Parent-Child 开关默认关闭。关闭时，知识库继续使用原有单层 chunk 处理和存储语义。

### 4.2 创建知识库确认页

创建知识库的第 3/3 步只读展示已经选择的配置结果。在分块策略右侧增加 Parent-Child 状态：

```text
父子层级分块：已启用
父子层级分块：未启用
```

确认页不提供修改能力。用户需要返回第 2/3 步修改。

如果稀疏向量输出开关适用于当前模型，确认页也应展示其最终状态，避免用户无法确认创建请求实际采用的配置。

### 4.3 入库参数配置

在入库参数配置的分块策略下方增加 Parent-Child 开关。开关关闭时，继续使用现有单层切块参数和链路。

开关打开时，配置项显示为：

| 配置项 | 默认值 | 限制 |
|---|---:|---|
| 父块 Token 数 | `1000` | 必须大于子块 Token 数。 |
| 子块 Token 数 | `200` | 必须小于父块 Token 数。 |
| 子块重叠比例（%） | `15` | 范围为 `0% - 100%`；实际重叠后的子块 Token 数不得超过子块 Token 上限。 |
| 分隔符 | `\\n` | 沿用当前分块策略对应的分隔符配置。 |

Token 计算规则如下：

- 父子切块场景使用当前嵌入模型 tokenizer 计算 Token。
- 其他既有场景继续使用项目当前统一 tokenizer。
- `start_offset` 和 `end_offset` 使用 Python 字符索引，不能改为 UTF-8 字节偏移。

参数的配置归属和优先级如下：

1. 每个知识库保存一套父子切块默认参数。
2. 新建文件入库任务默认继承知识库参数。
3. 单文件入库接口允许传入任务级切块参数。
4. 任务级参数优先于知识库默认参数，只影响本次文件任务。
5. 修改知识库默认参数只影响未来新入库文件，不自动影响已入库文件。

当父块 Token 数不大于子块 Token 数、重叠比例超出范围或其他切块前置条件不成立时，后端必须明确返回参数错误，不得静默使用另一组默认值。

### 4.4 知识库详情页

知识库详情页继续展示当前分块策略，并增加 Parent-Child 的已启用/未启用状态。检索配置面板根据后端下发的条件动态显示以下新配置。

四个新增开关的默认状态均为关闭：

| 开关 | 默认状态 |
|---|---|
| 稀疏向量输出 | 关闭 |
| Parent-Child | 关闭 |
| 向量加权聚合 | 关闭 |
| RRF | 关闭 |

#### 向量加权聚合

当稀疏向量输出已启用，且检索模式为“向量检索”或“混合检索”时，显示“向量加权聚合”开关及以下两个权重配置：

- 稠密向量权重，默认 `0.7`。
- 稀疏向量权重，默认 `0.3`。

Parent-Child 可以独立于稀疏向量开关启用。稀疏向量单独启用时仍允许使用向量加权聚合；Parent-Child 不应成为加权聚合的强制前置条件。

用户输入的原始权重允许在 `[0.0, 5.0]` 范围内，不要求两者之和等于 `1.0`。后端在计算阶段自动归一化：

```text
sum_w = dense_weight + sparse_weight
dense_w_norm = dense_weight / sum_w
sparse_w_norm = sparse_weight / sum_w
```

用户界面继续显示用户输入的原始值。例如输入 `0.7` 和 `0.5` 时保留这两个值；归一化只发生在后端评分阶段。

两个权重中只有一个为 `0` 时，另一个归一化为 `1`，等价于只使用对应的一路检索。两个权重同时为 `0` 时，后端拒绝请求。

加权聚合只融合稠密向量分数和 BGE-M3 稀疏向量分数，不把 BM25 或图检索分数直接加入该公式。两路分数在加权前分别在本次查询的候选集内执行 Min-Max 归一化并映射到 `[0, 1]`，再计算：

```text
final_score = dense_w_norm * dense_score_normalized
            + sparse_w_norm * sparse_score_normalized
```

Milvus 索引要求如下：

- 稠密向量字段使用 `IVF_FLAT`，度量类型使用内积 `IP`。
- BGE-M3 稀疏向量字段使用 `SPARSE_INVERTED_INDEX`，度量类型使用内积 `IP`。
- `nlist` 等与数据规模相关的索引参数不在本需求中固定为具体数值，由后续详细设计根据真实数据规模确定。

#### RRF 倒排融合

此处“RRF 倒排融合”指 Reciprocal Rank Fusion，不是新增一种倒排索引。

RRF 开关的显示条件如下：

| 检索模式 | 图检索 | 显示 RRF 开关 |
|---|---:|---:|
| 向量检索 | 关闭 | 否 |
| 向量检索 | 开启 | 是 |
| BM25 全文检索 | 关闭 | 否 |
| BM25 全文检索 | 开启 | 是 |
| 混合检索 | 关闭 | 是 |
| 混合检索 | 开启 | 是 |

RRF 与向量加权聚合相互独立：

- 向量加权聚合只处理稠密向量和 BGE-M3 稀疏向量两路结果。
- RRF 在本次需求范围内只融合三路结果：向量内聚合结果、BM25 结果和图检索结果；不额外引入未确认的召回器。
- 在同时满足条件时，两个开关可以同时启用。

RRF 必须发生在重排序之前。目标链路为：

```text
多路召回
  -> 向量加权聚合（如启用）
  -> RRF 融合与去重（如启用）
  -> Rerank 重排序（如启用）
  -> 最终候选输出
```

启用 RRF 时，重排序只能接收 RRF 产生的统一候选列表，不得直接接收各路原始召回列表。RRF 使用排名倒数累加：

```text
RRF_score = Σ 1 / (60 + rank)
```

其中 `rank` 从 `1` 开始，常数固定为 `60`。第一名的贡献为 `1 / 61`；该公式保持当前实现的等价写法。

### 4.5 Parent-Child 检索结果

Parent-Child 模式下，Top-K 分为两个独立概念：

- `top_k_child`：Milvus 内部召回的子块数量，例如 `30`。该值应大于最终父块数量。
- `top_k_parent`：按 `parent_id` 去重后输出给上层业务的唯一父块数量。

检索流程要求如下：

1. Milvus 召回 `top_k_child` 个子块。
2. 如果启用 RRF，先以 `child_id` 为候选身份，对向量、BM25 和图检索三路执行子块粒度 RRF；不能提前按 `parent_id` 去重。
3. 如果启用重排序，Rerank 接收 RRF 后的统一子块候选及其子块文本，对子块打分重排。
4. RRF 和 Rerank 完成后，依据 `parent_id` 聚合命中的子块。
5. 同一父块命中多个子块时，父块分数取该父块命中子块中的最高分。
6. 按父块分数排序并截取 `top_k_parent` 个唯一父块。
7. 从命中的子块中提取父块 ID，读取对应的完整 `parent_text`。
8. 传给 LLM 的上下文只包含去重后的完整父块文本，不包含子块文本。
9. 对外返回结果除父块文本外，必须保留命中的子块信息，包括 `child_id`、子块分数、子块在父块内的偏移和页码等可用元数据，用于来源引用和前端片段高亮定位。

## 5. 数据与存储需求

### 5.1 PostgreSQL

Parent-Child 模式新增独立的父块和子块持久化模型，避免把父块完整上下文混入现有单层 chunk 的语义中。

建议的数据关系如下，最终表名和约束由详细设计确认：

```text
parent_chunk
  parent_id UUID PRIMARY KEY
  parent_text TEXT NOT NULL
  doc_id 独立文档 ID
  start_offset Python 字符起始偏移
  end_offset Python 字符结束偏移
  metadata 文档全局元数据

child_chunk
  child_id UUID PRIMARY KEY
  parent_id UUID NOT NULL REFERENCES parent_chunk(parent_id)
  doc_id 独立文档 ID
  child_text TEXT NOT NULL
  chunk_index 父块内的子块序号
  metadata 文档、文件、页码和定位元数据
```

最低数据要求：

- PostgreSQL 保存父块完整原文、`parent_id` 主键、文档元数据和父子映射关系。
- `parent_id` 与 `child_id` 使用 UUID 字符串语义；数据库主键类型和序列化格式在详细设计中统一。
- `doc_id` 是新增的独立文档标识，不等同于现有 `file_id`；父块和子块仍需保存与现有文件记录的 `file_id` 关系，以便兼容现有文件边界。
- 父块记录必须包含 `parent_id`、`parent_text`、`doc_id`、`start_offset`、`end_offset` 和 `metadata`。
- 父块删除、文档删除和文档重切时，父子记录必须保持可验证的级联或显式清理关系。
- 现有单层 `knowledge_chunks` 及其历史图谱引用关系不得被无意破坏。Parent-Child 模式下父块是图谱语义主体，子块是其下属节点，Neo4j 关系边由父块指向子块；历史单层 chunk 通过映射兼容，不直接替代新的图谱主体。

### 5.2 Milvus

Parent-Child 模式使用独立 collection，例如 `rag_child_chunk`；父块不写入 Milvus。

子块 collection 至少包含：

```text
child_id
parent_id
child_text
dense_vector
sparse_vector
chunk_index
meta_info
```

其中：

- `child_id` 是子块唯一主键。
- `parent_id` 是连接 PostgreSQL 父块的关键外键。
- `child_text` 是检索和向量化文本。
- `dense_vector` 的维度由嵌入模型实际返回值决定，不固定为 `1024`。
- `sparse_vector` 只在 BGE-M3 且启用稀疏向量输出时生成。
- `meta_info` 至少能够回溯知识库、文档、文件、上传时间、标签、页码和父块内定位信息。

现有知识库不自动改用新的 child collection。新建或显式重建的文档根据当时有效配置选择对应的存储链路。

### 5.3 Redis 推荐缓存

Redis 只承担可过期、可丢失的短期缓存，不拥有父块、检索结果或会话的最终业务事实。

推荐缓存：

| 缓存 | 用途 | 默认 TTL | 失效要求 |
|---|---|---:|---|
| 热点父块文本缓存 | 减少频繁读取 PostgreSQL 的父块正文 | `3600s` | 允许 TTL 自动过期；文档删除和重切时主动清理关联缓存。 |
| 检索结果缓存 | 缓存相同查询的向量/RRF 结果 | `600s` | 文档删除或重切时清理命中该文档的缓存；知识库配置变化不主动清理已有文档缓存。 |
| 会话缓存 | 缓存 RAG 会话上下文 | `86400s` | 会话超时后自动失效。 |

缓存失败、缓存未命中或 Redis 不可用时必须回源 PostgreSQL 或重新执行检索，不能把缓存异常伪装为业务成功，也不能把 Redis 当作最终状态来源。

## 6. 入库与重建行为

### 6.1 新文档入库

新文档进入解析、切块和索引流程时，根据知识库默认配置与任务级覆盖参数确定最终切块模式。Parent-Child 开启时，入库流程必须同时产生父块记录、子块记录和子块向量；任一关键写入失败时，文件状态和可观察错误必须反映失败原因。

当同时启用 Parent-Child 和稀疏-稠密向量输出时，数据职责如下：

```text
PostgreSQL：父块完整原文、parent_id、文档元数据、父子映射关系
Redis：热点父块文本、检索结果和会话短期缓存
Milvus：子块、dense_vector、sparse_vector；不保存父块
```

### 6.2 已有文档

系统升级后禁止后台自动触发存量知识库和历史文档的数据迁移或向量重建。存量切片、向量和现有检索行为保持不变。

如果用户希望旧文档使用新切块规则，产品需要提供显式的“文档重新切片入库”操作。该操作必须明确作用范围，并按以下语义执行：

1. 基于目标文档和当前生效参数生成新的父块、子块、稠密向量和可选稀疏向量版本。
2. 新版本的 PostgreSQL 父子记录和 Milvus 子块记录全部写入，并回读确认数量、关联关系和关键字段完整。
3. 新版本验证成功后，再切换该文档的有效版本，并清理旧父块、子块、Milvus 记录及其关联缓存。
4. 新版本任一步失败时，不切换有效版本，保留旧数据继续提供检索，并将本次重切标记为失败供用户重试。

知识库默认参数变更本身不触发上述操作。

## 7. 兼容性要求

### 7.1 旧知识库和旧字段

旧知识库和旧文档没有 `parent_id`、`sparse_vector` 等新字段时，检索必须继续工作，不能因为新字段缺失而报错或中断。

兼容规则如下：

- 缺少 `sparse_vector` 时，跳过稀疏向量召回分支，继续使用可用的稠密向量、BM25、图检索等链路。
- 缺少 `parent_id` 时，回退到现有单层 chunk 检索和返回逻辑。
- 新字段均为非强制扩展字段，不能成为存量文档检索成功的强制依赖。
- 未启用新能力或未填充新字段的旧数据，升级后的行为与升级前保持一致。
- 兼容回退必须由后端字段存在性、collection schema 或数据版本判断执行，不能依靠前端隐藏选项实现。

### 7.2 模型和索引兼容

嵌入模型和向量维度属于索引的一部分。不同向量空间不能在同一检索链路中混用。新模型、维度或 collection 的重建只能由用户显式触发，并需要在实际存储和检索结果上验证。

### 7.3 LITE 模式

LITE 模式启动、readiness、自检和能力发现阶段不得加载或初始化 Milvus、图谱引擎、Parent-Child 向量 collection 或稀疏向量推理运行时。完整模式才注册和使用这些能力；能力未就绪时必须结构化暴露，不得返回看似成功的空检索结果。

## 8. 检索链路要求

### 8.1 非 Parent-Child 模式

非 Parent-Child 模式沿用当前单层 chunk 语义：召回结果以现有 chunk 为单位，向量、BM25、图检索和重排序的现有行为继续有效。新增加权聚合和 RRF 时，只有满足各自开关条件的链路才参与计算。

混合检索分为两个独立层级：第一层是稠密/稀疏向量的向量内混合，第二层是向量结果与 BM25、图检索之间的跨范式融合。

| 稀疏向量 | RRF | 第一层：向量内混合 | 第二层：跨范式混合 |
|---|---|---|---|
| 关闭 | 关闭 | 无，直接使用稠密向量 | 稠密向量 + BM25，继续使用升级前的 `vector_weight / bm25_weight` 加权融合 |
| 开启 | 关闭 | 稠密 + 稀疏，使用向量加权聚合 | 聚合向量 + BM25，继续使用 `vector_weight / bm25_weight` 加权融合 |
| 开启 | 开启 | 稠密 + 稀疏，使用向量加权聚合 | 聚合向量、BM25 和图检索结果使用全链路 RRF |

关闭 BGE-M3 稀疏向量时，且新增 RRF 也关闭时，系统完全退化为升级前的原有检索逻辑，不引入新的向量内混合步骤，也不改变原有 `vector_weight / bm25_weight` 行为。新增 RRF 关闭时，当前图检索已有的内部排名融合必须继续保留；新增 RRF 开关只控制全链路 RRF，不删除或替换现有图检索内部融合。

RRF 与稀疏向量输出相互独立。稀疏向量关闭但 RRF 开启时，向量一路直接使用稠密召回结果，再与 BM25、图检索结果按 RRF 融合；不得为了启用 RRF 强制开启稀疏向量或向量加权聚合。

### 8.2 Parent-Child 模式

Parent-Child 模式以子块作为召回、融合和重排序对象，以父块作为最终去重和 LLM 上下文对象。所有向量、BM25 和图检索结果都必须映射为可追溯的 `child_id` 子块候选后再参与融合。

当多个检索路径命中同一父块时，必须在 RRF 和 Rerank 完成后按 `parent_id` 去重。命中子块信息继续附着在父块结果上，供来源引用、页码展示和片段高亮使用。

### 8.3 RRF 路径

启用 RRF 时，各路召回结果先保留自己的排名，再按统一候选身份融合：

```text
向量召回（稠密 + 稀疏加权聚合结果）
BM25 召回
图检索召回
    -> child_id 粒度 RRF 融合
    -> 可选 Rerank（使用子块文本）
    -> parent_id 聚合去重
    -> top_k_parent
    -> 读取 parent_text 供给 LLM
```

本次需求没有其他召回器。某一路因配置关闭、无结果或旧数据不支持而缺失时，RRF 使用其余可用召回结果；不得把缺失召回分支伪装成一组空但成功的新能力结果。

## 9. API、配置与数据契约影响

本提案只确定语义，不锁定最终字段命名。后续详细设计至少需要覆盖以下契约。

### 9.1 创建知识库请求

创建请求需要能表达：

- 嵌入模型标识。
- 是否启用 BGE-M3 稀疏向量输出。
- 现有分块策略 ID。
- 是否启用 Parent-Child。
- 知识库级父块 Token 数、子块 Token 数、子块重叠比例和分隔符。

后端需要根据嵌入模型能力校验稀疏向量配置，不接受非 BGE-M3 模型的无效稀疏配置。

### 9.2 文件入库请求

文件入库、重新索引或显式重切请求需要支持任务级切块参数覆盖知识库默认值，并在文件的处理参数中保存本次实际采用的配置，便于重试、审计和结果回读。

### 9.3 检索查询请求

查询请求需要支持临时覆盖知识库默认的：

- `top_k_child` 和 `top_k_parent`。
- 是否启用向量加权聚合。
- 稠密向量原始权重和稀疏向量原始权重。
- 是否启用 RRF。
- 现有向量、BM25、图检索和重排序参数。

临时查询参数只影响本次查询，不改写知识库持久化配置。后端必须对临时参数执行与知识库配置相同的边界校验。

### 9.4 返回结果

Parent-Child 返回结果需要同时表达：

- 唯一父块 ID和完整父块文本。
- 父块最终分数以及用于解释的融合/重排分数。
- 关联命中的子块列表。
- 子块 ID、子块分数、父块内偏移、页码和来源元数据。

非 Parent-Child 旧返回结构必须继续兼容，已有调用方不能被迫读取 Parent-Child 专属字段。

## 10. 前端影响范围

后续实现至少需要更新以下用户路径：

1. `DatabaseCreateFlowModal.vue`：模型能力条件展示稀疏向量开关，展示独立 Parent-Child 开关，并在第 3/3 步展示最终状态。
2. `databaseCreateForm.js`：保存默认值、条件值、校验和请求组装逻辑。
3. `DataBaseInfoView.vue`：展示和编辑知识库级 Parent-Child 配置及其状态。
4. `SearchConfigPanel.vue`：根据后端依赖条件展示向量加权聚合、两个向量权重和 RRF 开关。
5. `knowledge_api.js`：保持知识库接口集中封装，补充后续确定的请求和响应字段。
6. 检索结果组件：Parent-Child 模式下展示父块上下文和命中子块来源定位信息，同时保持旧 chunk 结果兼容。

前端只负责交互、条件展示和输入体验；权限、模型能力、参数关系和数据隔离必须在后端执行。

## 11. 权限、失败与可观察性

- 创建、修改知识库配置、修改检索配置和显式重切文档继续使用现有知识库管理权限。
- 查询和读取父块、子块及来源信息继续使用现有知识库可见性查询和后端权限依赖。
- 不得因为前端隐藏 Parent-Child、稀疏向量或 RRF 控件而跳过后端校验。
- PostgreSQL、Milvus 或 Redis 任一关键操作失败时，必须记录可诊断的失败结果和影响对象；Redis 失败可以按缓存语义降级，PostgreSQL/Milvus 的核心数据写入不能静默成功。
- 检索结果必须绑定本次请求实际命中的父块/子块，不能从相邻查询或相邻文档猜测父块文本。
- 显式重切操作必须可观察到目标文档、旧数据清理结果、新数据写入结果和最终文件状态。

## 12. 测试与证据要求

本需求涉及前端交互、配置契约、持久化、Milvus、检索排序和兼容回退，后续实现不能只用 unit 测试证明完成。

### 12.1 Unit

至少覆盖：

- BGE-M3 模型标识及供应商前缀匹配；非 BGE-M3 不显示/不接受稀疏配置。
- Parent-Child 参数默认值和边界校验。
- 父块必须大于子块。
- 重叠比例范围和实际子块 Token 上限。
- 知识库默认参数与任务级参数的覆盖优先级。
- 稠密/稀疏权重归一化、单路权重为零和双零拒绝。
- 候选集内 Min-Max 归一化的空集合、单值集合和全相同分数场景。
- Parent-Child 按 `parent_id` 去重、父块最高子块分数和 `top_k_child`/`top_k_parent` 截取。
- RRF 排名分数、重复候选合并和缺少某一路召回结果。

### 12.2 PostgreSQL integration

至少覆盖：

- 父块、子块和父子外键关系的真实写入、读取和删除。
- 文档删除和显式重切时父子数据不会残留或错误串联。
- 知识库默认参数和文件任务级覆盖参数的真实持久化。
- 旧 `knowledge_chunks` 数据不需要新字段也能被现有查询读取。

### 12.3 API integration

至少覆盖真实 HTTP：

- 创建知识库时保存新配置并在详情接口读回。
- 非 BGE-M3 请求携带稀疏配置时被后端拒绝。
- 检索配置面板读取到正确的条件参数定义。
- 不满足条件时不返回向量加权聚合或 RRF 配置。
- 满足条件时返回正确默认值和已保存值。
- 无管理权限时不能修改配置或触发显式重切。

### 12.4 Milvus/检索 integration 或 E2E

至少覆盖真实或项目规定的确定性 assembled path：

- BGE-M3 稀疏向量与稠密向量写入子块 collection，父块不写入 Milvus。
- Milvus collection 的稠密和稀疏索引字段、索引类型和度量类型符合要求。
- 子块召回后能读取唯一父块并向 LLM 提供完整父块文本。
- 返回结果保留命中子块定位信息。
- RRF 在 Rerank 前执行，Rerank 不接收各路原始召回列表。
- 旧 collection 缺少 `sparse_vector` 或 `parent_id` 时回退并保持查询可用。
- 新配置不触发旧文档自动迁移或自动重建。

### 12.5 Web unit、build 与真实页面

至少覆盖：

- 创建页的模型条件展示、开关状态和第 3/3 步摘要。
- 入库参数开关开闭时字段显示和校验。
- 检索配置中向量加权聚合与 RRF 的条件显示。
- 用户输入原始权重在保存后仍保持原始值展示。
- loading、empty、error、旧数据缺字段和 Parent-Child 结果定位状态。
- 执行前端 lint、unit、build，并在真实页面检查关键流程。

## 13. 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 | 负向案例 |
|---|---|---|---|---|
| BGE-M3 才显示并接受稀疏向量输出配置 | 非 BGE-M3 错误启用稀疏向量 | 创建表单、创建 API、模型能力校验 | Web unit + 真实 HTTP integration + PostgreSQL 回读 | 非 BGE-M3 携带稀疏配置被拒绝 |
| Parent-Child 是独立开关并沿用六种底层策略 | 新增错误的独立下拉策略或绕过底层策略 | 创建表单、chunking dispatcher、入库 service | Web unit + 切块结果回读 | 选择任意底层策略并关闭 Parent-Child 时只生成旧单层 chunk |
| 父子参数按知识库默认、任务覆盖生效 | 修改默认值错误重切存量文档或任务覆盖不生效 | 知识库配置与文件入库 service | PostgreSQL integration + 入库 E2E | 修改知识库默认参数后旧文档向量数量或正文被后台改变 |
| 父块只在 PostgreSQL，子块和向量在 Milvus | 父块写入向量库或子块缺少父外键 | PostgreSQL/Milvus schema Owner | 两侧真实数据回读 | Milvus 出现父块记录或子块无法回溯父块 |
| Parent-Child 检索子块召回、父块供给 | 返回多个同父块结果或把子块直接送入 LLM | Milvus executor、父块 repository、结果 schema | 检索 E2E + Prompt/协议结果回读 | 同一父块命中多个子块后仍返回重复父块 |
| 向量加权聚合只融合归一化后的稠密/稀疏分数 | 分数未归一化、权重双零或把 BM25/图分数混入 | 检索 executor | unit + 检索 integration | 两个权重同时为零仍执行查询 |
| RRF 在 Rerank 前融合统一候选 | Rerank 直接接收多路原始召回 | 检索编排与重排调用边界 | deterministic replay/E2E + 结果回读 | Rerank 输入包含未经过 RRF 的多路列表 |
| 旧数据不因缺新字段而中断 | 旧 collection 或旧 chunk 读取时报错 | 兼容查询路径 | 旧数据 integration | 缺少 `sparse_vector` 或 `parent_id` 时查询失败 |
| LITE 不加载知识库重运行时 | 轻量模式启动时初始化 Milvus、图谱或稀疏推理 | runtime composition、capability discovery | LITE 启动/readiness 检查 | LITE 能力发现触发知识库模块初始化 |

## 14. 风险与未决问题

### 已确认风险

- 稠密向量和稀疏向量分数分布不同，必须在本次候选集内分别 Min-Max 归一化；候选集为空、只有一个值或所有值相同的处理需要后续详细设计固定并测试。
- Parent-Child 会增加 PostgreSQL 父子记录、Milvus 子块数量和查询后的父块回读压力，需要根据真实文档规模验证 `top_k_child`、`top_k_parent` 和批量大小。
- Parent-Child 会改变图谱节点和检索候选的映射：父块作为图谱语义主体、子块作为下属节点，历史 `knowledge_chunks.chunk_id` 通过映射兼容；详细设计仍需固定映射表、删除边界和查询回溯规则。
- Milvus 已有 BM25 稀疏字段与 BGE-M3 稀疏向量具有不同语义，字段命名和 collection schema 必须避免把 BM25 生成字段与模型输出的 sparse vector 混用。
- 新旧 collection 并存时，查询路径需要明确根据知识库配置和 collection schema 选择，不能通过失败后静默切换到另一种数据语义。

### 必须在详细设计前确认的问题

1. Milvus 新 collection 的命名规则、生命周期、删除条件和多 collection 查询方式需要由详细设计固定。
2. 父块和子块 Token 数的最小值、最大值、整数要求、`100%` 重叠比例的步长处理、超限时拒绝还是缩短重叠，需要在详细设计中固定。
3. tokenizer 的具体实现、版本以及 tokenizer 不可用时的失败处理需要固定；当前仅确认父子切块使用嵌入模型 tokenizer。
4. 跨页元数据来源、子块重叠时字符偏移的唯一定位规则、父块 Markdown/空白规范化规则需要固定。

## 15. 后续阶段输入

后续详细设计文档必须以本提案的已确认需求为输入，逐项补齐：

- 最终配置字段名、类型、默认值、版本和兼容读取规则。
- 创建、更新、入库、重切、查询和结果返回 API 契约。
- PostgreSQL 表、外键、索引、删除策略和幂等写入边界。
- Milvus collection schema、字段命名、索引参数和 collection 选择逻辑。
- Parent-Child chunking 算法如何复用六种底层策略，以及 Token/字符偏移如何精确保存。
- 向量加权聚合、Min-Max 边界、RRF 常数和多路候选身份统一规则。
- 图谱与 Parent-Child 的关系、Redis 缓存 key/TTL/失效策略和失败恢复。
- 真实 PostgreSQL、Milvus、HTTP、worker、前端页面和旧数据兼容验证路径。

## 16. 当前文档验证状态

本提案只生成需求文档，没有修改代码、Schema、Compose 或测试，也没有执行产品链路测试。现状入口和 Owner 为 `Inspected`；新增功能的实现、数据写入、检索结果、页面行为和兼容回退均为 `Not run`，不能视为已实现或已通过验收。
