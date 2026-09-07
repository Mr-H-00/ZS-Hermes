# 本地 BGE-M3 原生稠密稀疏向量 Provider

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/embed.py

## 问题

知识库配置可以开启 `bge_m3_sparse_enabled`，Milvus 写入与查询链路可以消费 dense 向量和 BGE-M3 lexical sparse 权重。embedding selector 需要一个明确的本地 provider，使 `rag_qa/models/bge-m3` 中的 BGE-M3 权重、tokenizer 与 `sparse_linear.pt` 能在同一次模型推理中产出 dense embedding 和模型原生 sparse 权重。

## 决策

`local` provider 类型只暴露 embedding 能力，并且只接受 `BAAI/bge-m3` 模型。内置 `local-bge-m3` provider 默认指向 `rag_qa/models/bge-m3`，默认禁用，启用后通过模型规格 `local-bge-m3:BAAI/bge-m3` 被 `select_embedding_model()` 选择。

本地 BGE-M3 provider 使用已有 `torch` 与 `transformers` 加载本地文件，不新增 FlagEmbedding 依赖。provider 首次编码时惰性加载 encoder、tokenizer 和 `sparse_linear.pt`；dense 向量使用 CLS pooling 并做 L2 normalize；sparse 权重使用 `sparse_linear(last_hidden_state)` 后 ReLU，再按 token id 取最大值并过滤 mask 与特殊 token。连接探针只检查目录、tokenizer 与 sparse head 形状，不加载完整 encoder。

HTTP embedding provider 保持现有行为；只有 provider 显式声明 `provider_type=local` 才进入完整本地 BGE-M3 dense+sparse 路径。远端 BGE-M3 spec 的 hybrid provider 只复用这里的本地 sparse 头和 tokenizer，不会把 remote provider 变成完整本地 provider。

## 替代方案

- 继续要求远端 OpenAI-compatible embedding 接口返回 `sparse_embedding`：代码更少，但无法使用仓库内的本地模型目录，且 sparse 字段不是 OpenAI-compatible 通用契约。
- 引入 FlagEmbedding：更贴近上游 BGE-M3 API，但增加依赖、锁文件和运行时维护面；当前 dense 与 lexical sparse 产出可以由已有 `torch` 与 `transformers` 完成。
- 在 Milvus 层使用 BM25 sparse：可以保留混合检索形态，但不是 BGE-M3 native sparse，也不能保证 dense 与 sparse 来自同一模型推理。

## 后果

本地 BGE-M3 推理会加载约 2.1GB 权重，CPU 环境下首次调用慢且内存占用高，因此本地 provider 只能在显式选择时惰性加载。模型目录、tokenizer、`pytorch_model.bin` 或 `sparse_linear.pt` 缺失时，连接测试和实际索引流程显式失败。

Provider 管理允许 `local` 类型，但 service 层 fail-closed 校验其能力与模型范围，避免 local provider 被扩展成未定义的本地 chat、rerank 或其他 embedding 后端。

`RemoteBGEM3DenseLocalSparseEmbedding` 复用本地 sparse 实现时仍依赖同一 `rag_qa/models/bge-m3` 目录，因此该目录的挂载既是完整本地 provider 的前提，也是远端 BGE-M3 sparse 组合的前提。

## 验证

- `rag_qa/models/bge-m3` 包含 `pytorch_model.bin`、`sparse_linear.pt`、`colbert_linear.pt`、tokenizer 文件与 SentencePiece 模型；本地探针确认 tokenizer vocab size 为 `250002`，`sparse_linear.pt` 权重形状为 `(1, 1024)`。
- 单条本地推理探针返回 dense 维度 `1024`，并产出非空 native sparse 权重。
- `uv run pytest test/unit/services/test_embedding_model_selectors.py test/unit/services/test_model_provider_service.py` 通过，覆盖 local provider 选择、remote BGE-M3 hybrid provider 选择、dense-only 不加载本地 sparse、sparse 组合与 batch 顺序、轻量连接探针、local provider service 规范化和更新拒绝。
- `uv run ruff check package/yuxi/models/embed.py package/yuxi/models/providers/service.py package/yuxi/models/providers/builtin.py package/yuxi/models/providers/cache.py test/unit/services/test_embedding_model_selectors.py test/unit/services/test_model_provider_service.py` 通过。
- `git diff --check` 通过。
- `python scripts/verify_engineering_contracts.py` 使用 bundled Python 通过。
- `docker compose exec api uv run --group test pytest test/unit -m "not slow"` 未执行：Docker daemon 未运行。
- `pnpm run lint:check` 未完成：本机 Node 版本低于 pnpm 依赖要求，切换 bundled Node 后依赖安装访问网络失败。
