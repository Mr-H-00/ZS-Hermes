# 远端 BGE-M3 稠密与本地原生稀疏组合 Provider

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/embed.py

## 问题

远端 BGE-M3 embedding API 负责 dense，但当 spec 是 `BAAI/bge-m3` 或 `Pro/BAAI/bge-m3` 且开启 sparse 时，系统需要继续使用远端 dense，同时从本地 `rag_qa/models/bge-m3` 生成 native sparse。远端响应里的 sparse 字段不再作为必需契约。

## 决策

`select_embedding_model()` 在远端 BGE-M3 spec 上返回 `RemoteBGEM3DenseLocalSparseEmbedding`：普通 `encode/aencode` 只走远端 API；`aencode_with_sparse/abatch_encode_with_sparse` 走远端 dense 再叠加本地 sparse；`test_connection()` 同时校验远端 dense 可用性和本地 sparse 目录可用性。非 BGE-M3 远端 embedding provider 保持原有行为，本地 `local-bge-m3` provider 继续负责完整的本地 dense+sparse。

组合 provider 复用本地 `LocalBGEM3Embedding` 的 sparse 逻辑，但只在 sparse 或连接探针需要时触发本地模型加载。`api` 和 `worker` 的 Compose 配置都挂载只读的 `./rag_qa/models:/app/rag_qa/models:ro`，让容器内能访问本地模型目录。

## 替代方案

- 继续要求远端 API 返回 sparse：代码更少，但当前远端接口不提供稳定的 native sparse。
- 远端 dense 失败时静默切到本地 dense：会改变用户选择的 embedding provider，并可能造成向量空间不一致。
- 在 Milvus 层自行生成 BM25 sparse：不能满足 BGE-M3 native sparse 的语义要求。

## 后果

远端 BGE-M3 的 sparse 组合依赖本地模型目录、tokenizer 与 sparse head，因此容器部署必须显式挂载 `rag_qa/models`。组合 provider 不会吞掉远端 dense 或本地 sparse 的失败；任一部分不可用时，索引与查询会显式失败。

## 验证

- 通过 bundled Python shim 跑通 `test/unit/services/test_embedding_model_selectors.py`，覆盖远端 hybrid provider 选择、dense-only 不加载本地 sparse、dense+sparse 组合、batch 顺序、本地 sparse-only batch、连接探针和非 BGE 远端 provider 保持原行为。
- `docker compose config` 通过，确认 `api` 与 `worker` 都挂载了 `./rag_qa/models:/app/rag_qa/models:ro`。
- `git diff --check` 通过。
- `uv run python scripts/verify_engineering_contracts.py` 通过。
- `uv run python -m unittest scripts.test_verify_engineering_contracts` 已执行，但当前 Windows 临时目录权限阻止 `tempfile` 写入，失败与本次实现无关。
