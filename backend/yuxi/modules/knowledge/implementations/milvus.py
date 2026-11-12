import asyncio
import inspect
import json
import math
import os
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    LexicalHighlighter,
    WeightedRanker,
    connections,
    db,
    utility,
)

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.modules.knowledge.base import FileStatus, KBEmbeddingModelMissingError, KnowledgeExecutor
from yuxi.modules.knowledge.chunking.dispatcher import IngestionChunk, chunk_markdown
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.modules.knowledge.chunking.ragflow_like.parent_child import chunk_markdown_parent_child
from yuxi.modules.knowledge.config_normalization import is_bge_m3_embedding_model_spec, resolve_feature_processing_params
from yuxi.modules.knowledge.parent_child_cache import (
    active_versions_fingerprint,
    cache_parent,
    cache_query,
    get_cached_parent,
    get_cached_query,
    invalidate_parent_cache,
    invalidate_parent_version,
    invalidate_query_cache,
    query_fingerprint,
)
from yuxi.modules.knowledge.read_models import KnowledgeBaseConfig
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.parent_child_chunk_repository import KnowledgeParentChildChunkRepository
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.storage.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    content_fields,
    create_collection,
    embedding_field,
    get_embedding_function,
)
from yuxi.modules.knowledge.storage.offload import run_milvus_query_io
from yuxi.modules.models.providers.cache import model_cache
from yuxi.modules.system.options import system_options
from yuxi.shared.asyncio_utils import run_sync_with_deferred_cancellation
from yuxi.shared.hashing import hashstr

CHILD_COLLECTION_PREFIX = "rag_child_chunk_"
CHILD_DENSE_FIELD = "dense_vector"
CHILD_SPARSE_FIELD = "bge_m3_sparse_vector"
CHILD_TEXT_FIELD = "child_text"
CHILD_KB_FIELD = "knowledge_base_id"

MILVUS_CHUNK_EMBED_BATCH_SIZE = 200
# Milvus 为派生索引：PG 先行落库（权威源），Milvus upsert 失败有限次重试，
# 重试耗尽则抛错由上层标记文件状态；不回滚 PG（避免单边数据）。
# 重新索引时 PG batch_upsert 与 Milvus upsert 均按主键幂等，重跑即可收敛。
MILVUS_CHUNK_UPSERT_ATTEMPTS = 3
MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS = 1.0


class _CommittedParentChildIndex(RuntimeError):
    """携带已经激活的 Parent-Child 结果与提交后的异常。"""

    def __init__(self, result: dict[str, Any], cause: BaseException) -> None:
        """保存已提交结果，供公开索引入口传播提交后异常。"""
        self.result = result
        self.cause = cause
        super().__init__(str(cause))


async def _await_with_deferred_cancellation(awaitable) -> tuple[Any, bool]:
    """等待关键提交任务取得确定结果，并报告期间收到的外层取消。"""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


@dataclass(kw_only=True)
class MilvusRetrievalConfig:
    search_mode: str = field(
        default="vector",
        metadata={
            "label": "检索模式",
            "type": "select",
            "options": [
                {"value": "vector", "label": "向量检索", "description": "仅使用向量相似度检索"},
                {"value": "keyword", "label": "BM25 全文检索", "description": "仅使用 Milvus BM25 检索"},
                {"value": "hybrid", "label": "混合检索", "description": "Milvus 向量检索与 BM25 融合检索"},
            ],
            "description": "选择检索模式",
        },
    )
    required_terms: str = field(
        default="",
        metadata={"label": "必含词", "type": "string", "description": "空格分隔；每个词项都必须匹配，按分词结果判断"},
    )
    excluded_terms: str = field(
        default="",
        metadata={"label": "排除词", "type": "string", "description": "空格分隔；排除包含任一词项的片段"},
    )
    exact_phrase: str = field(
        default="",
        metadata={"label": "完整短语", "type": "string", "description": "按分词后的顺序匹配完整短语，不允许间隔词项"},
    )
    highlight_results: bool = field(
        default=True,
        metadata={"label": "命中高亮", "type": "boolean", "description": "展示关键词和文本筛选条件命中的片段"},
    )
    final_top_k: int = field(
        default=10,
        metadata={
            "label": "最终返回 Chunk 数",
            "type": "number",
            "min": 1,
            "max": 100,
            "description": "重排序后返回给前端的文档数量",
        },
    )
    similarity_threshold: float = field(
        default=0.0,
        metadata={
            "label": "向量相似度阈值（0-1）",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "仅对纯向量检索生效，过滤低于此值的结果",
        },
    )
    bm25_top_k: int = field(
        default=50,
        metadata={
            "label": "BM25 召回数量",
            "type": "number",
            "min": 1,
            "max": 200,
            "description": "BM25 全文检索和混合检索中的 BM25 候选数量",
        },
    )
    vector_weight: float = field(
        default=0.7,
        metadata={
            "label": "向量检索权重",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "混合检索中向量召回结果的融合权重",
        },
    )
    bm25_weight: float = field(
        default=0.3,
        metadata={
            "label": "BM25 权重",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "混合检索中 BM25 召回结果的融合权重",
        },
    )
    bm25_drop_ratio_search: float = field(
        default=0.0,
        metadata={
            "label": "BM25 稀疏项丢弃比例",
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "step": 0.1,
            "description": "BM25 检索时丢弃低分稀疏项的比例，数值越大检索越快但可能降低召回",
        },
    )
    include_distances: bool = field(
        default=True,
        metadata={"label": "显示相似度", "type": "boolean", "description": "在结果中显示相似度分数"},
    )
    use_graph_retrieval: bool = field(
        default=False,
        metadata={"label": "启用图检索", "type": "boolean", "description": "是否启用实体和三元组扩散检索"},
    )
    graph_entity_top_k: int = field(
        default=10,
        metadata={
            "label": "图实体召回数量",
            "type": "number",
            "min": 1,
            "max": 100,
            "depend_on": ("use_graph_retrieval", True),
            "description": "通过 Query 召回的实体数量",
        },
    )
    graph_triple_top_k: int = field(
        default=10,
        metadata={
            "label": "图三元组召回数量",
            "type": "number",
            "min": 1,
            "max": 100,
            "depend_on": ("use_graph_retrieval", True),
            "description": "通过 Query 召回的三元组数量",
        },
    )
    graph_max_nodes: int = field(
        default=10000,
        metadata={
            "label": "图检索最大节点数",
            "type": "number",
            "min": 100,
            "max": 50000,
            "depend_on": ("use_graph_retrieval", True),
            "description": "2-hop 扩散子图最多读取的节点数量",
        },
    )
    graph_top_k: int = field(
        default=20,
        metadata={
            "label": "图召回 Chunk 数",
            "type": "number",
            "min": 1,
            "max": 200,
            "depend_on": ("use_graph_retrieval", True),
            "description": "PPR 后从图谱路径召回的 Chunk 数量",
        },
    )
    graph_weight: float = field(
        default=1.0,
        metadata={
            "label": "图检索融合权重",
            "type": "number",
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "depend_on": ("use_graph_retrieval", True),
            "description": "排名融合时图检索结果的权重",
        },
    )
    ppr_damping: float = field(
        default=0.85,
        metadata={
            "label": "PPR 阻尼系数",
            "type": "number",
            "min": 0.1,
            "max": 0.99,
            "step": 0.01,
            "depend_on": ("use_graph_retrieval", True),
            "description": "Personalized PageRank 的阻尼系数",
        },
    )
    use_reranker: bool = field(
        default=False,
        metadata={"label": "启用重排序", "type": "boolean", "description": "是否使用精排模型对检索结果进行重排序"},
    )
    reranker_model: str = field(
        default="",
        metadata={
            "label": "重排序模型",
            "type": "select",
            "depend_on": ("use_reranker", True),
            "description": "选择用于本次查询的重排序模型",
            "options_provider": "rerank_models",
        },
    )
    recall_top_k: int = field(
        default=50,
        metadata={
            "label": "召回数量",
            "type": "number",
            "min": 10,
            "max": 200,
            "depend_on": ("use_reranker", True),
            "description": "向量检索或混合检索保留的候选数量（启用重排序时有效）",
        },
    )

    top_k_child: int = field(
        default=30,
        metadata={
            "label": "子块召回数量",
            "type": "number",
            "min": 1,
            "max": 500,
            "description": "Parent-Child 检索进入融合前保留的子块数量",
        },
    )

    top_k_parent: int = field(
        default=10,
        metadata={
            "label": "父块返回数量",
            "type": "number",
            "min": 1,
            "max": 100,
            "description": "Parent-Child 检索最终返回的父块数量",
        },
    )

    use_vector_score_fusion: bool = field(
        default=False,
        metadata={
            "label": "稠密与稀疏向量融合",
            "type": "boolean",
            "visible_when": {"any": [{"search_mode": "vector"}, {"search_mode": "hybrid"}]},
            "description": "对稠密向量与 BGE-M3 稀疏向量分数加权融合",
        },
    )

    dense_vector_weight: float = field(
        default=0.7,
        metadata={
            "label": "稠密向量权重",
            "type": "number",
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "depend_on": ("use_vector_score_fusion", True),
            "description": "稠密向量分数的原始融合权重",
        },
    )

    sparse_vector_weight: float = field(
        default=0.3,
        metadata={
            "label": "稀疏向量权重",
            "type": "number",
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "depend_on": ("use_vector_score_fusion", True),
            "description": "BGE-M3 稀疏向量分数的原始融合权重",
        },
    )

    use_rrf: bool = field(
        default=False,
        metadata={
            "label": "RRF 倒排融合",
            "type": "boolean",
            "visible_when": {"any": [{"search_mode": "hybrid"}, {"use_graph_retrieval": True}]},
            "description": "在重排序前以固定常数 60 融合向量、BM25 与图检索排名",
        },
    )


def _retrieval_config_options() -> list[dict[str, Any]]:
    options = []
    for config_field in fields(MilvusRetrievalConfig):
        metadata = dict(config_field.metadata)
        options_provider = metadata.pop("options_provider", None)
        default = None if config_field.default is MISSING else config_field.default
        option = {
            "key": config_field.name,
            "default": default,
            **metadata,
        }
        if options_provider == "rerank_models":
            option["options"] = [{"label": info.display_name, "value": info.spec} for info in model_cache.get_all_specs("rerank")]
        options.append(option)
    return options


class MilvusKB(KnowledgeExecutor):
    """基于 Milvus 的生产级向量库"""

    MAX_FINAL_TOP_K = 100
    MAX_RECALL_TOP_K = 200
    MAX_BM25_TOP_K = 200
    MAX_GRAPH_ENTITY_TOP_K = 100
    MAX_GRAPH_TRIPLE_TOP_K = 100
    MAX_GRAPH_TOP_K = 200
    MAX_GRAPH_NODES = 50_000

    kb_type = "milvus"
    name = "Milvus"
    description = "基于 Milvus 的生产级向量知识库，适合高性能部署"

    def __init__(self, work_dir: str, **kwargs):
        """初始化 Milvus 知识库并建立连接。"""
        super().__init__(work_dir)

        self.milvus_token = kwargs.get("milvus_token", os.getenv("MILVUS_TOKEN") or "")
        self.milvus_uri = kwargs.get("milvus_uri", os.getenv("MILVUS_URI") or "http://localhost:19530")
        self.milvus_db = kwargs.get("milvus_db") or os.getenv("MILVUS_DB") or "yuxi"

        self.connection_alias = f"milvus_{hashstr(work_dir, 6)}"

        # 存储集合映射 {kb_id: Collection}
        self.collections: dict[str, Any] = {}
        self.child_collections: dict[str, Any] = {}
        from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

        self.graph_service = KnowledgeGraphService()

        self._init_connection()
        logger.info("MilvusKB initialized")

    def __del__(self):
        """清理连接"""
        try:
            if hasattr(self, "connection_alias"):
                connections.disconnect(self.connection_alias)
        except Exception:  # noqa: S110
            pass

    # ==== 文件写入：索引 ====

    async def index_file(self, kb_id: str, file_id: str, *args, **kwargs) -> dict:
        """持有投影写锁直到所有 I/O 完成，清理不能被旧 attempt 的晚写越过。"""
        from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

        async with knowledge_projection_lock(kb_id, shared=True):
            return await self._index_file(kb_id, file_id, *args, **kwargs)

    # ==== 检索 ====

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        **kwargs,
    ) -> list[dict]:
        """异步查询知识库"""
        feature_options = {**asdict(MilvusRetrievalConfig()), **config.query_options, **kwargs}
        if (config.additional_params.get("parent_child") or {}).get("enabled") is True:
            return await self._aquery_parent_child(query_text, kb_id, config=config, merged_kwargs=feature_options)
        embedding_model_spec = config.embedding_model_spec
        collection = await self._get_existing_milvus_collection(kb_id)
        if not collection:
            if await KnowledgeChunkRepository().count_by_kb_id(kb_id):
                raise ValueError(f"Knowledge base {kb_id} has no vector projection; reindex is required")
            return []

        # 合并查询参数：kwargs（单次临时参数）优先级高于 config.query_options（持久化配置）
        merged_kwargs = {**asdict(MilvusRetrievalConfig()), **config.query_options, **kwargs}

        if merged_kwargs.get("use_vector_score_fusion") or merged_kwargs.get("use_rrf"):
            return await self._aquery_single_features(query_text, kb_id, config=config, collection=collection, merged_kwargs=merged_kwargs)

        try:
            logger.debug(f"Query params: {merged_kwargs}")
            final_top_k = min(max(int(merged_kwargs["final_top_k"]), 1), self.MAX_FINAL_TOP_K)
            similarity_threshold = float(merged_kwargs["similarity_threshold"])
            include_distances = bool(merged_kwargs["include_distances"])
            search_mode = str(merged_kwargs["search_mode"]).lower()
            if search_mode not in {"vector", "keyword", "hybrid"}:
                raise ValueError("Unsupported Milvus search mode")

            use_reranker = bool(merged_kwargs["use_reranker"])
            use_graph_retrieval = bool(merged_kwargs["use_graph_retrieval"])
            if use_reranker or use_graph_retrieval:
                recall_top_k = min(
                    max(int(merged_kwargs["recall_top_k"]), final_top_k),
                    self.MAX_RECALL_TOP_K,
                )
            else:
                recall_top_k = final_top_k

            active_expr = await self._build_active_generation_expr(kb_id)
            file_expr = await self._build_file_name_expr(kb_id, merged_kwargs.get("file_name"))
            text_expr, highlighter = self._build_text_search(merged_kwargs, search_mode, query_text)
            filter_expr = " and ".join(f"({expr})" for expr in (active_expr, file_expr, text_expr) if expr)

            output_fields = ["content", "chunk_id", "file_id", "generation", "chunk_index"]
            retrieved_chunks: list[dict] = []
            if search_mode == "vector":
                embedding_function = get_embedding_function(embedding_model_spec, sync=True)
                query_embedding = await run_milvus_query_io(embedding_function, [query_text])

                search_params = {"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}}

                results = await run_milvus_query_io(
                    collection.search,
                    data=query_embedding,
                    anns_field="embedding",
                    param=search_params,
                    limit=recall_top_k,
                    expr=filter_expr,
                    output_fields=output_fields,
                    highlighter=highlighter,
                )

                for hit in results[0] if results else []:
                    if hit.distance < similarity_threshold:
                        continue
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, hit.distance, include_distances))

                logger.debug(f"Milvus vector query response: {len(retrieved_chunks)} chunks found (after similarity filtering)")

            elif search_mode == "keyword":
                bm25_top_k = min(
                    max(int(merged_kwargs["bm25_top_k"]), 1),
                    self.MAX_BM25_TOP_K,
                )
                bm25_drop_ratio_search = float(merged_kwargs["bm25_drop_ratio_search"])
                bm25_search_params = {
                    "metric_type": "BM25",
                    "params": {"drop_ratio_search": bm25_drop_ratio_search},
                }

                results = await run_milvus_query_io(
                    collection.search,
                    data=[query_text],
                    anns_field=CONTENT_SPARSE_FIELD,
                    param=bm25_search_params,
                    limit=bm25_top_k,
                    expr=filter_expr,
                    output_fields=output_fields,
                    highlighter=highlighter,
                )

                for hit in results[0] if results else []:
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, hit.distance, include_distances, score_field="bm25_score"))

                logger.debug(f"Milvus BM25 query response: {len(retrieved_chunks)} chunks found")
            else:
                embedding_function = get_embedding_function(embedding_model_spec, sync=True)
                query_embedding = await run_milvus_query_io(embedding_function, [query_text])
                bm25_top_k = min(
                    max(int(merged_kwargs["bm25_top_k"]), 1),
                    self.MAX_BM25_TOP_K,
                )
                bm25_drop_ratio_search = float(merged_kwargs["bm25_drop_ratio_search"])
                vector_weight = float(merged_kwargs["vector_weight"])
                bm25_weight = float(merged_kwargs["bm25_weight"])

                vector_request = AnnSearchRequest(
                    data=query_embedding,
                    anns_field="embedding",
                    param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
                    limit=recall_top_k,
                    expr=filter_expr,
                )
                bm25_request = AnnSearchRequest(
                    data=[query_text],
                    anns_field=CONTENT_SPARSE_FIELD,
                    param={
                        "metric_type": "BM25",
                        "params": {"drop_ratio_search": bm25_drop_ratio_search},
                    },
                    limit=bm25_top_k,
                    expr=filter_expr,
                )
                results = await run_milvus_query_io(
                    collection.hybrid_search,
                    reqs=[vector_request, bm25_request],
                    rerank=WeightedRanker(vector_weight, bm25_weight),
                    limit=recall_top_k,
                    output_fields=output_fields,
                )
                for hit in results[0] if results else []:
                    score = float(hit.distance or 0.0)
                    retrieved_chunks.append(self._build_chunk_from_hit(hit, score, include_distances, score_field="hybrid_score"))

                if highlighter and retrieved_chunks:
                    # 3.0.2 的 hybrid_search 不返回高亮；在已召回 ID 内取文本高亮，保持融合分数与顺序。
                    chunk_ids = [chunk["metadata"]["chunk_id"] for chunk in retrieved_chunks]
                    highlighted = await run_milvus_query_io(
                        collection.search,
                        data=query_embedding,
                        anns_field="embedding",
                        param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
                        limit=len(chunk_ids),
                        expr=f"chunk_id in {json.dumps(chunk_ids)}",
                        output_fields=output_fields,
                        highlighter=highlighter,
                    )
                    highlights_by_id = {
                        hit.entity.get("chunk_id"): self._build_chunk_from_hit(hit, hit.distance, False).get("highlights")
                        for hit in highlighted[0]
                    }
                    for chunk in retrieved_chunks:
                        if fragments := highlights_by_id.get(chunk["metadata"]["chunk_id"]):
                            chunk["highlights"] = fragments

                logger.debug(f"Milvus hybrid query response: {len(retrieved_chunks)} chunks found")

            if use_graph_retrieval:
                graph_chunks = await self._retrieve_graph_chunks(
                    query_text,
                    kb_id,
                    retrieved_chunks,
                    merged_kwargs,
                    embedding_model_spec,
                )
                if graph_chunks and filter_expr:
                    chunk_ids = [chunk["metadata"]["chunk_id"] for chunk in graph_chunks]
                    allowed = await run_milvus_query_io(
                        collection.query,
                        expr=f"({filter_expr}) and chunk_id in {json.dumps(chunk_ids)}",
                        output_fields=["chunk_id"],
                    )
                    allowed_ids = {chunk["chunk_id"] for chunk in allowed}
                    graph_chunks = [chunk for chunk in graph_chunks if chunk["metadata"]["chunk_id"] in allowed_ids]
                if graph_chunks:
                    graph_weight = float(merged_kwargs["graph_weight"])
                    retrieved_chunks = self._fuse_chunk_rankings(retrieved_chunks, graph_chunks, graph_weight)

            if not retrieved_chunks:
                return []

            retrieved_chunks = await self._hydrate_chunk_sources(kb_id, retrieved_chunks)

            if not use_reranker:
                return retrieved_chunks[:final_top_k]

            reranker_model = merged_kwargs["reranker_model"]
            if not reranker_model:
                raise ValueError(
                    "Reranker model must be specified when use_reranker=True. Please provide reranker_model in query parameters."
                )

            try:
                from yuxi.modules.models.rerank import get_reranker

                reranker = get_reranker(reranker_model)
                try:
                    rerank_start = time.time()
                    documents_text = [chunk["content"] for chunk in retrieved_chunks]
                    rerank_scores = await reranker.acompute_score([query_text, documents_text], normalize=True)

                    for chunk, rerank_score in zip(retrieved_chunks, rerank_scores):
                        chunk["rerank_score"] = float(rerank_score)

                    retrieved_chunks.sort(key=lambda item: item.get("rerank_score", item.get("score", 0.0)), reverse=True)
                    elapsed = time.time() - rerank_start
                    logger.info(f"Reranking completed for {kb_id} in {elapsed:.3f}s with model {reranker_model}")
                finally:
                    await reranker.aclose()

            except Exception as exc:  # noqa: BLE001
                logger.error(f"Reranking failed: {exc}, falling back to vector scores")

            return retrieved_chunks[:final_top_k]

        except Exception as e:
            logger.error(f"Milvus query error: {e}, {traceback.format_exc()}")
            raise

    @classmethod
    def get_query_params_config(cls) -> dict:
        """获取 Milvus 知识库的查询参数配置"""
        return {"type": "milvus", "options": _retrieval_config_options()}

    # ==== 清理与运维 ====

    async def delete_file_chunks_only(
        self, kb_id: str, file_id: str, generation: int | None = None, *, update_file_stats: bool = True
    ) -> None:
        """删除指定代次的派生 Chunk；不传 generation 时删除文件全部代次。"""
        parent_child_repo = KnowledgeParentChildChunkRepository()
        await self.graph_service.delete_file_graph(kb_id, file_id, generation=generation)
        for version in await parent_child_repo.list_versions_for_generation(kb_id, file_id, generation):
            collection = self._get_existing_child_collection(int(version.embedding_dimension))
            if collection is not None:
                await self._delete_child_version_from_milvus(collection, kb_id, version.version_id)
            await parent_child_repo.delete_version_projection(version.version_id)
        if generation is None:
            await self._delete_file_child_chunks_from_all_collections(kb_id, file_id)
        await invalidate_parent_cache(kb_id)
        await invalidate_query_cache(kb_id)
        chunk_repo = KnowledgeChunkRepository()
        collection = await self._get_existing_milvus_collection(kb_id)

        if collection:
            try:
                await self._delete_file_chunks_from_milvus(collection, file_id, generation)
            except Exception as e:
                logger.error(f"Error checking file existence in Milvus: {e}")
                raise
        # 外部删除成功后再移除 chunk 事实，失败时保留可重试的元数据。
        if generation is None:
            await chunk_repo.delete_by_file_id(file_id)
        else:
            await chunk_repo.delete_by_file_id(file_id, generation=generation)
        if generation is None and update_file_stats:
            await KnowledgeFileRepository().update_fields(
                file_id=file_id,
                kb_id=kb_id,
                data={"chunk_count": 0, "token_count": 0},
            )

    async def cleanup_knowledge_base_resources(self, kb_id: str) -> dict:
        """清理知识库资源，同时删除 Milvus 集合。"""

        await self._delete_kb_child_chunks_from_all_collections(kb_id)
        await invalidate_parent_cache(kb_id)
        await invalidate_query_cache(kb_id)
        vector_store = await self.graph_service.get_graph_vector_store()

        def delete_milvus_collections() -> None:
            try:
                if utility.has_collection(kb_id, using=self.connection_alias):
                    utility.drop_collection(kb_id, using=self.connection_alias)
                    logger.info(f"Dropped Milvus collection for {kb_id}")
                else:
                    logger.info(f"Milvus collection {kb_id} does not exist, skipping")
            except Exception as e:
                logger.error(f"Failed to drop Milvus collection {kb_id}: {e}")
                raise

            vector_store.drop_graph_collections(kb_id)

        await await_io(asyncio.to_thread(delete_milvus_collections))

        return await super().cleanup_knowledge_base_resources(kb_id)

    # ==== 内部辅助：连接与集合管理 ====

    def _init_connection(self):
        """在本连接作用域内创建并选择目标数据库。"""
        connections.connect(alias=self.connection_alias, uri=self.milvus_uri, token=self.milvus_token)
        if self.milvus_db not in db.list_database(using=self.connection_alias):
            db.create_database(self.milvus_db, using=self.connection_alias)
        db.using_database(self.milvus_db, using=self.connection_alias)
        logger.info(f"Connected to Milvus at {self.milvus_uri}, database={self.milvus_db}")

    async def _get_or_create_milvus_collection(self, kb_id: str, embedding_model_spec: str | None):
        """获取或创建 Milvus 集合"""
        if kb_id in self.collections:
            return self.collections[kb_id]

        try:
            collection = await self._create_kb_instance(kb_id, embedding_model_spec)
            await self._initialize_kb_instance(collection)

            self.collections[kb_id] = collection
            return collection

        except KBEmbeddingModelMissingError:
            raise
        except Exception as e:
            logger.error(f"Failed to create Milvus collection for {kb_id}: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    async def _get_existing_milvus_collection(self, kb_id: str) -> Collection | None:
        """获取已存在的集合，不因删除操作创建新集合。"""
        collection = self.collections.get(kb_id)
        if collection is not None:
            return collection

        def load_existing_collection() -> Collection | None:
            if not utility.has_collection(kb_id, using=self.connection_alias):
                return None
            return Collection(name=kb_id, using=self.connection_alias)

        return await asyncio.to_thread(load_existing_collection)

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None) -> Any:
        """在线程中创建或加载 Milvus 集合，避免阻塞 worker heartbeat。"""
        return await await_io(asyncio.to_thread(self._create_kb_instance_sync, kb_id, embedding_model_spec))

    def _create_kb_instance_sync(self, kb_id: str, embedding_model_spec: str | None) -> Any:
        """同步创建或加载 Milvus 集合。"""
        logger.info(f"Creating Milvus collection for {kb_id}")

        if not embedding_model_spec:
            raise KBEmbeddingModelMissingError()

        embedding_info = model_cache.get_model_info(embedding_model_spec)
        if not embedding_info or embedding_info.model_type != "embedding":
            raise ValueError(f"Unsupported embedding model: {embedding_model_spec}")

        collection_name = kb_id

        if not utility.has_collection(collection_name, using=self.connection_alias):
            return self._create_new_collection(collection_name, embedding_info, kb_id)

        collection = Collection(name=collection_name, using=self.connection_alias)
        expected_description = f"Knowledge base collection for {kb_id} using {embedding_info.model_id}"
        # 局部命名与导入的 embedding_field 构造函数区分开：这里检查已建集合的 schema。
        embedding_field_schema = next((field for field in collection.schema.fields if field.name == "embedding"), None)
        if (
            collection.description != expected_description
            or embedding_field_schema is None
            or embedding_field_schema.params.get("dim") != (embedding_info.dimension or 1024)
            or not self._collection_supports_text_retrieval(collection)
        ):
            raise RuntimeError(f"Collection {collection_name} does not match the knowledge base schema or model")
        return collection

    def _create_new_collection(self, collection_name: str, embedding_info: Any, kb_id: str) -> Collection:
        """创建新的 Milvus 集合"""
        embedding_dim = embedding_info.dimension or 1024
        model_name = embedding_info.model_id

        collection = create_collection(
            collection_name,
            fields=[
                *content_fields(),
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                embedding_field(embedding_dim),
                FieldSchema(name="generation", dtype=DataType.INT64),
                FieldSchema(name=CHILD_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
            ],
            description=f"Knowledge base collection for {kb_id} using {model_name}",
            using=self.connection_alias,
        )

        collection.create_index(
            CHILD_SPARSE_FIELD,
            {"metric_type": "IP", "index_type": "SPARSE_INVERTED_INDEX", "params": {"inverted_index_algo": "DAAT_MAXSCORE"}},
        )
        logger.info(f"Created new Milvus collection: {collection_name} '{model_name=}', {embedding_dim=}")

        return collection

    def _collection_supports_text_retrieval(self, collection: Collection) -> bool:
        """检查集合是否具备 BM25 与文本匹配所需的 schema。"""
        fields = {field.name: field for field in collection.schema.fields}
        content_field = fields.get("content")
        sparse_field = fields.get(CONTENT_SPARSE_FIELD)
        generation_field = fields.get("generation")
        if not generation_field or generation_field.dtype != DataType.INT64:
            return False
        if not content_field or content_field.dtype != DataType.VARCHAR:
            return False
        if not content_field.params.get("enable_analyzer") or not content_field.params.get("enable_match"):
            return False
        if not sparse_field or sparse_field.dtype != DataType.SPARSE_FLOAT_VECTOR:
            return False

        for function in collection.schema.functions:
            if (
                function.type == FunctionType.BM25
                and function.input_field_names == ["content"]
                and function.output_field_names == [CONTENT_SPARSE_FIELD]
            ):
                return True
        return False

    async def _initialize_kb_instance(self, instance: Any) -> None:
        """初始化 Milvus 集合（加载到内存）"""
        await asyncio.to_thread(instance.load)
        logger.info("Milvus collection loaded into memory")

    # ==== 内部辅助：文件索引写入与删除 ====

    async def _index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> dict:
        """索引文件：文件夹直接失败；可入库文件先声明为 INDEXING。"""
        file_meta = await documents.load_file_meta(kb_id, file_id)
        # done 同时是文件夹就绪值，必须在声明前拒绝，不能只靠状态集合区分。
        if file_meta.get("is_folder"):
            raise ValueError(f"Cannot index folder '{file_id}'")
        params = {
            **(params or {}),
            **resolve_feature_processing_params(additional_params, file_meta.get("processing_params"), params, embedding_model_spec),
        }
        # 在投影锁内先检查 PG 可见事实，删除后不能重新创建外部集合。
        collection = await self._get_or_create_milvus_collection(kb_id, embedding_model_spec)
        if not collection:
            raise ValueError(f"Failed to get Milvus collection for {kb_id}")

        embedding_function = get_embedding_function(embedding_model_spec)

        # done/failed 是历史文件值，分别与 indexed、error_indexing 一样可重新声明。
        allowed_statuses = {
            FileStatus.PARSED,
            FileStatus.ERROR_INDEXING,
            FileStatus.INDEXED,
            "done",
            "failed",
        }
        file_repo = KnowledgeFileRepository()
        owner_filter = (
            {"processing_job_id": processing_job_id, "processing_owner": processing_owner}
            if processing_job_id is not None and processing_owner is not None
            else {}
        )

        claim_data = {
            "status": FileStatus.INDEXING,
            "error_message": None,
            "processing_job_id": processing_job_id,
            "processing_owner": processing_owner,
        }
        if operator_id:
            claim_data["updated_by"] = operator_id

        claimed_record = await file_repo.update_fields_if_status(
            kb_id=kb_id,
            file_id=file_id,
            allowed_statuses=allowed_statuses,
            data=claim_data,
            processing_params=params,
            kb_additional_params=additional_params,
        )
        if claimed_record is None:
            current_meta = await documents.load_file_meta(kb_id, file_id)
            current_status = current_meta.get("status")
            raise ValueError(
                f"Cannot index file with status '{current_status}'. File must be parsed first "
                f"(status should be one of: {', '.join(allowed_statuses)})"
            )

        file_meta = documents.file_record_to_meta(claimed_record)
        params = dict(file_meta.get("processing_params") or {})
        if not file_meta.get("markdown_file"):
            reset_data = {
                "status": FileStatus.UPLOADED,
                "error_message": None,
                "processing_job_id": None,
                "processing_owner": None,
            }
            if operator_id:
                reset_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                kb_id=kb_id,
                file_id=file_id,
                allowed_statuses={FileStatus.INDEXING},
                data=reset_data,
                **owner_filter,
            )
            if updated_record is None and processing_owner is not None:
                raise asyncio.CancelledError("File processing owner was lost")
            raise ValueError("File has not been parsed yet (no markdown_file)")

        try:
            generation = await file_repo.begin_index_generation(kb_id=kb_id, file_id=file_id, **owner_filter)
            file_meta["generation"] = generation
            chunk_parser_config = dict(params.get("chunk_parser_config") or {})
            chunk_parser_config.setdefault("embed_model_id", (await system_options.get())["embed_model"])
            params["chunk_parser_config"] = chunk_parser_config
            markdown_content = await documents.read_markdown_from_minio(file_meta["markdown_file"])
            filename = file_meta.get("filename")

            if params.get("indexing_path") == "parent_child":
                return await self._index_parent_child_file(
                    kb_id=kb_id,
                    file_id=file_id,
                    file_meta=file_meta,
                    params=params,
                    embedding_model_spec=embedding_model_spec,
                    embedding_function=embedding_function,
                    processing_job_id=processing_job_id,
                    processing_owner=processing_owner,
                    operator_id=operator_id,
                    markdown_content=markdown_content,
                )
            chunks = self._split_text_into_chunks(markdown_content, file_id, filename, params)
            for chunk in chunks:
                chunk["generation"] = generation
                chunk["chunk_id"] = f"{chunk['chunk_id'][:80]}-g{generation}"[:100]
            logger.info(
                f"Split {filename} into {len(chunks)} chunks with params: "
                f"chunk_preset_id={params.get('chunk_preset_id')}, "
                f"chunk_parser_config={params.get('chunk_parser_config')}"
            )

            chunk_stats = self._calculate_chunk_stats(chunks)
            await self._embed_and_store_chunks(
                kb_id,
                file_id,
                collection,
                chunks,
                embedding_function,
                sparse_enabled=(params.get("embedding_features") or {}).get("bge_m3_sparse_enabled") is True,
            )

            logger.info(f"Indexed file {file_id} generation {generation} into Milvus")

            updated_record = await file_repo.finish_index_generation(
                kb_id=kb_id,
                file_id=file_id,
                generation=generation,
                **chunk_stats,
                **owner_filter,
            )
            return documents.file_record_to_meta(updated_record)

        except _CommittedParentChildIndex as committed:
            raise committed.cause
        except (Exception, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    current_task.uncancel()
            error_msg = "File indexing was cancelled" if isinstance(e, asyncio.CancelledError) else str(e)
            logger.error(f"Indexing failed for {file_id}: {error_msg}")
            update_data = {
                "status": FileStatus.ERROR_INDEXING,
                "error_message": error_msg,
                "processing_job_id": None,
                "processing_owner": None,
                "projection_status": "failed",
                "projection_error": error_msg,
            }
            if operator_id:
                update_data["updated_by"] = operator_id
            updated_record = await file_repo.update_fields_if_status(
                file_id=file_id,
                kb_id=kb_id,
                allowed_statuses={FileStatus.INDEXING},
                data=update_data,
                **owner_filter,
            )
            if updated_record is None and processing_owner is not None:
                raise asyncio.CancelledError("File processing owner was lost")
            raise

    def _split_text_into_chunks(self, text: str, file_id: str, filename: str, params: dict) -> list[IngestionChunk]:
        """将文本分割成块"""
        return chunk_markdown(text, file_id, filename, params)

    def _calculate_chunk_stats(self, chunks: list[IngestionChunk]) -> dict[str, int]:
        """统计 chunk 数量与 token 总数。"""
        return {
            "chunk_count": len(chunks),
            "token_count": sum(count_tokens(chunk["content"]) for chunk in chunks),
        }

    async def _embed_and_store_chunks(
        self,
        kb_id: str,
        file_id: str,
        collection: Collection,
        chunks: list[dict],
        embedding_function,
        *,
        chunk_batch_size: int = MILVUS_CHUNK_EMBED_BATCH_SIZE,
        sparse_enabled: bool = False,
    ) -> None:
        """对 chunks 进行分批嵌入并存储到 Milvus 和 PostgreSQL"""
        if not chunks:
            return

        chunk_batch_size = max(int(chunk_batch_size), 1)
        for start in range(0, len(chunks), chunk_batch_size):
            batch_chunks = chunks[start : start + chunk_batch_size]
            texts = [chunk["content"] for chunk in batch_chunks]
            sparse_embeddings = None
            if sparse_enabled:
                model = getattr(embedding_function, "__self__", None) or getattr(
                    getattr(embedding_function, "func", None), "__self__", None
                )
                sparse_encoder = getattr(model, "abatch_encode_with_sparse", None)
                if not callable(sparse_encoder):
                    raise ValueError("BGE-M3 sparse 已启用，但 embedding provider 未提供 sparse 输出")
                embeddings, sparse_embeddings = await sparse_encoder(texts)
            else:
                embeddings = await embedding_function(texts)
            if sparse_embeddings is None:
                await self._insert_chunks_to_stores(kb_id, file_id, collection, batch_chunks, embeddings)
            else:
                await self._insert_chunks_to_stores(
                    kb_id,
                    file_id,
                    collection,
                    batch_chunks,
                    embeddings,
                    sparse_embeddings=sparse_embeddings,
                )

    def _build_chunk_pg_records(self, kb_id: str, chunks: list[IngestionChunk]) -> list[dict[str, Any]]:
        """把 chunk 列表映射为 PG batch_upsert 记录。"""
        return [
            {
                "chunk_id": chunk["chunk_id"],
                "file_id": chunk["file_id"],
                "kb_id": kb_id,
                "chunk_index": chunk["chunk_index"],
                "generation": int(chunk.get("generation", 1) or 1),
                "content": chunk["content"],
                "start_line": chunk.get("start_line"),
                "end_line": chunk.get("end_line"),
                "start_char_pos": chunk.get("start_char_pos"),
                "end_char_pos": chunk.get("end_char_pos"),
                "start_token_pos": chunk.get("start_token_pos"),
                "end_token_pos": chunk.get("end_token_pos"),
                "graph_indexed": bool(chunk.get("graph_indexed", False)),
                "ent_ids": chunk.get("ent_ids"),
                "tags": chunk.get("tags"),
                "extraction_result": chunk.get("extraction_result"),
            }
            for chunk in chunks
        ]

    async def _insert_chunks_to_stores(
        self,
        kb_id: str,
        file_id: str,
        collection: Collection,
        chunks: list[IngestionChunk],
        embeddings: list,
        *,
        sparse_embeddings: list[dict[int, float]] | None = None,
    ) -> None:
        """先落 PG 权威事实，再把派生向量 upsert 进 Milvus。"""
        if not chunks:
            return

        entities = [
            [chunk["chunk_id"] for chunk in chunks],
            [chunk["content"] for chunk in chunks],
            [chunk["chunk_id"] for chunk in chunks],
            [chunk["file_id"] for chunk in chunks],
            [chunk["chunk_index"] for chunk in chunks],
            embeddings,
            [int(chunk.get("generation", 1) or 1) for chunk in chunks],
        ]
        has_sparse = any(field.name == CHILD_SPARSE_FIELD for field in collection.schema.fields)
        if sparse_embeddings is not None and not has_sparse:
            raise ValueError("BGE-M3 sparse indexing requires a re-created sparse collection")
        if sparse_embeddings is not None and len(sparse_embeddings) != len(chunks):
            raise ValueError("BGE-M3 sparse vector count does not match chunks")
        if has_sparse:
            entities.append(
                [self._validate_child_sparse_vector(item) for item in sparse_embeddings]
                if sparse_embeddings is not None
                else [{} for _ in chunks]
            )
        chunk_repo = KnowledgeChunkRepository()

        def _upsert_milvus_records():
            collection.upsert(entities)

        # PG 为权威源，先落 chunk 事实；Milvus 只是派生索引，失败不回滚 PG。
        await chunk_repo.batch_upsert(self._build_chunk_pg_records(kb_id, chunks))

        last_error: Exception | None = None
        for attempt in range(MILVUS_CHUNK_UPSERT_ATTEMPTS):
            try:
                await await_io(asyncio.to_thread(_upsert_milvus_records))
                return
            except Exception as e:
                last_error = e
                logger.warning(f"Milvus chunk upsert failed for file {file_id} (attempt {attempt + 1}/{MILVUS_CHUNK_UPSERT_ATTEMPTS}): {e}")
                if attempt + 1 < MILVUS_CHUNK_UPSERT_ATTEMPTS:
                    await asyncio.sleep(MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS)

        assert last_error is not None
        raise last_error

    async def _delete_file_chunks_from_milvus(self, collection: Collection, file_id: str, generation: int | None = None) -> None:
        """删除文件在 Milvus 中的派生向量；文件不存在时跳过删除。"""
        expr = f'file_id == "{file_id}"'
        if generation is not None:
            expr += f" and generation == {int(generation)}"

        def delete_from_milvus() -> bool:
            results = collection.query(expr=expr, output_fields=["id"], limit=1)
            if not results:
                return False
            collection.delete(expr)
            return True

        if await await_io(asyncio.to_thread(delete_from_milvus)):
            logger.info(f"Deleted chunks for file {file_id} from Milvus")
        else:
            logger.info(f"File {file_id} not found in Milvus, skipping delete operation")

    # ==== 内部辅助：检索过滤与结果装配 ====

    async def _build_active_generation_expr(self, kb_id: str) -> str:
        """先过滤非服务代次，避免不可见候选占满 Milvus top-k。"""
        grouped: dict[int, list[str]] = {}
        for file_id, generation in await KnowledgeFileRepository().list_active_generations(kb_id):
            grouped.setdefault(generation, []).append(file_id)
        return (
            " or ".join(f"(generation == {generation} and file_id in {json.dumps(file_ids)})" for generation, file_ids in grouped.items())
            or "file_id in []"
        )

    async def _build_file_name_expr(self, kb_id: str, file_name: str | None) -> str | None:
        """把文件名筛选转换为 file_id 过滤表达式；无匹配时返回恒假表达式。"""
        if not file_name:
            return None

        matched_file_ids = await KnowledgeFileRepository().list_file_ids_by_filename_contains(
            kb_id=kb_id,
            filename_pattern=file_name,
        )
        if not matched_file_ids:
            return 'file_id == "__no_matching_file__"'
        escaped_ids = [file_id.replace('"', '\\"') for file_id in matched_file_ids]
        if len(escaped_ids) == 1:
            return f'file_id == "{escaped_ids[0]}"'
        joined_ids = '", "'.join(escaped_ids)
        return f'file_id in ["{joined_ids}"]'

    def _build_text_search(
        self, options: dict[str, Any], search_mode: str, query_text: str = ""
    ) -> tuple[str | None, LexicalHighlighter | None]:
        """将文本条件转换为安全的过滤表达式与服务端高亮配置。"""
        text_options = {}
        for key in ("required_terms", "excluded_terms", "exact_phrase"):
            value = options.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            text_options[key] = value.strip()

        clauses = []
        highlight_query = []
        for term in text_options["required_terms"].split():
            clauses.append(f"TEXT_MATCH(content, {json.dumps(term, ensure_ascii=False)})")
            highlight_query.append({"type": "TextMatch", "field": "content", "text": term})
        for term in text_options["excluded_terms"].split():
            clauses.append(f"not TEXT_MATCH(content, {json.dumps(term, ensure_ascii=False)})")
        if phrase := text_options["exact_phrase"]:
            clauses.append(f"PHRASE_MATCH(content, {json.dumps(phrase, ensure_ascii=False)}, 0)")
            # 高亮协议仅支持 TextMatch；短语是否命中由上面的 PHRASE_MATCH 决定。
            highlight_query.append({"type": "TextMatch", "field": "content", "text": phrase})

        if search_mode == "hybrid" and query_text:
            highlight_query.append({"type": "TextMatch", "field": "content", "text": query_text})

        highlighter = None
        highlight_results = options.get("highlight_results", True)
        if not isinstance(highlight_results, bool):
            raise ValueError("highlight_results must be a boolean")
        if highlight_results and (search_mode != "vector" or highlight_query):
            highlighter = LexicalHighlighter(
                pre_tags=["\ue000"],
                post_tags=["\ue001"],
                highlight_search_text=search_mode == "keyword",
                highlight_query=highlight_query,
                fragment_offset=40,
                fragment_size=200,
                num_of_fragments=3,
            )
        return " and ".join(clauses) or None, highlighter

    def _build_chunk_from_hit(
        self,
        hit: Any,
        score: float,
        include_distances: bool,
        score_field: str | None = None,
    ) -> dict:
        """将 Milvus Hit 转成知识库统一返回的 Chunk 结构。"""
        entity = hit.entity
        file_id = entity.get("file_id")
        metadata = {
            "source": "未知来源",
            "chunk_id": entity.get("chunk_id"),
            "file_id": file_id,
            "generation": entity.get("generation", 1),
            "chunk_index": entity.get("chunk_index"),
        }
        chunk = {"content": entity.get("content", ""), "metadata": metadata, "score": float(score or 0.0)}
        chunk["score_type"] = {"bm25_score": "bm25", "hybrid_score": "hybrid"}.get(score_field, "cosine")
        highlight = getattr(hit, "highlight", None)
        if highlight and (fragments := highlight.get("content", {}).get("fragments")):
            chunk["highlights"] = [
                [
                    {"text": part[1:-1] if part.startswith("\ue000") else part, "matched": part.startswith("\ue000")}
                    for part in re.split(r"(\ue000.*?\ue001)", fragment, flags=re.DOTALL)
                    if part
                ]
                for fragment in fragments
            ]
        if score_field:
            chunk[score_field] = float(score or 0.0)
        if include_distances:
            chunk["distance"] = hit.distance
        return chunk

    async def _hydrate_chunk_sources(self, kb_id: str, chunks: list[dict]) -> list[dict]:
        """补充 chunk 来源文件名和总分片数，并过滤已从 PG 删除的孤儿 chunk。

        Milvus 中可能残留已删除文件的向量（如删除流程中途失败），
        检索侧依据 PG 中 file_id 是否存在进行过滤，防止已删除内容被返回。
        """
        file_ids = sorted({str(file_id) for chunk in chunks if (file_id := (chunk.get("metadata") or {}).get("file_id"))})
        if not file_ids:
            return []

        sources = await KnowledgeFileRepository().get_chunk_sources_by_file_ids(kb_id=kb_id, file_ids=file_ids)
        chunk_ids = [str((chunk.get("metadata") or {}).get("chunk_id") or "") for chunk in chunks]
        records = await KnowledgeChunkRepository().list_by_chunk_ids(chunk_ids)
        records_by_id = {record.chunk_id: record for record in records if record.kb_id == kb_id}
        live_chunks: list[dict] = []
        for chunk in chunks:
            metadata = chunk.get("metadata")
            if not isinstance(metadata, dict):
                continue
            file_id = str(metadata.get("file_id") or "")
            source = sources.get(file_id)
            if source is None:
                continue
            record = records_by_id.get(str(metadata.get("chunk_id") or ""))
            if record is None or record.file_id != file_id or record.generation != source["active_generation"]:
                continue
            chunk["content"] = record.content
            metadata.update(source)
            metadata["generation"] = record.generation
            metadata["chunk_index"] = record.chunk_index
            metadata["start_line"] = record.start_line
            metadata["end_line"] = record.end_line
            live_chunks.append(chunk)
        return live_chunks

    # ==== 内部辅助：图检索与排名融合 ====

    async def _retrieve_graph_chunks(
        self,
        query_text: str,
        kb_id: str,
        base_chunks: list[dict],
        query_params: dict[str, Any],
        embedding_model_spec: str | None,
    ) -> list[dict]:
        """图扩散召回候选 chunk；失败降级为空结果，不阻断主检索。"""
        try:
            if not embedding_model_spec:
                return []

            entity_top_k = min(
                max(int(query_params.get("graph_entity_top_k", 10)), 1),
                self.MAX_GRAPH_ENTITY_TOP_K,
            )
            triple_top_k = min(
                max(int(query_params.get("graph_triple_top_k", 10)), 1),
                self.MAX_GRAPH_TRIPLE_TOP_K,
            )
            graph_top_k = min(max(int(query_params.get("graph_top_k", 20)), 1), self.MAX_GRAPH_TOP_K)
            graph_max_nodes = min(max(int(query_params.get("graph_max_nodes", 10000)), 1), self.MAX_GRAPH_NODES)

            vector_store = await self.graph_service.get_graph_vector_store()
            entity_hits, triple_hits = await asyncio.gather(
                vector_store.search_entities(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=entity_top_k,
                ),
                vector_store.search_triples(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=triple_top_k,
                ),
            )
            seed_weights = await self._build_graph_seed_weights(kb_id, base_chunks, entity_hits, triple_hits)
            if not seed_weights:
                return []

            graph_scores = await self.graph_service.query_and_rank_chunks_by_ppr(
                kb_id,
                seed_weights,
                max_nodes=graph_max_nodes,
                top_k=graph_top_k,
                damping=float(query_params.get("ppr_damping", 0.85)),
            )
            if not graph_scores:
                return []

            chunks = await KnowledgeChunkRepository().list_by_chunk_ids([chunk_id for chunk_id, _ in graph_scores])
            score_by_chunk_id = dict(graph_scores)
            return [self._build_chunk_from_record(chunk, score_by_chunk_id[chunk.chunk_id], score_field="graph_score") for chunk in chunks]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Graph retrieval failed for {kb_id}: {exc}")
            return []

    async def _build_graph_seed_weights(
        self,
        kb_id: str,
        base_chunks: list[dict],
        entity_hits: list[dict[str, Any]],
        triple_hits: list[dict[str, Any]],
    ) -> dict[str, float]:
        """由实体、三元组与已召回 chunk 构建归一化的 PPR 种子权重。"""
        seed_weights: dict[str, float] = {}

        def add_seed(entity_id: str | None, score: float, weight: float) -> None:
            if not entity_id:
                return
            seed_weights[entity_id] = seed_weights.get(entity_id, 0.0) + max(float(score or 0.0), 0.0) * weight

        for hit in entity_hits:
            add_seed(hit.get("id"), hit.get("score", 0.0), 1.0)

        for hit in triple_hits:
            score = float(hit.get("score") or 0.0)
            add_seed(hit.get("source_id"), score, 0.8)
            add_seed(hit.get("target_id"), score, 0.8)

        chunk_scores = {
            chunk.get("metadata", {}).get("chunk_id"): float(chunk.get("score") or 0.0)
            for chunk in base_chunks
            if chunk.get("metadata", {}).get("chunk_id")
        }
        if chunk_scores:
            chunks = await KnowledgeChunkRepository().list_by_chunk_ids(list(chunk_scores))
            for chunk in chunks:
                for entity_id in chunk.ent_ids or []:
                    add_seed(entity_id, chunk_scores.get(chunk.chunk_id, 0.0), 0.3)

        total = sum(seed_weights.values())
        if total <= 0:
            return {}
        return {entity_id: weight / total for entity_id, weight in seed_weights.items()}

    def _build_chunk_from_record(self, chunk: Any, score: float, score_field: str | None = None) -> dict:
        """将 PG chunk 记录转成统一返回的 Chunk 结构。"""
        metadata = {
            "source": "未知来源",
            "chunk_id": chunk.chunk_id,
            "file_id": chunk.file_id,
            "chunk_index": chunk.chunk_index,
        }
        result = {"content": chunk.content, "metadata": metadata, "score": float(score or 0.0)}
        result["score_type"] = "graph"
        if score_field:
            result[score_field] = float(score or 0.0)
        return result

    def _fuse_chunk_rankings(
        self,
        base_chunks: list[dict],
        graph_chunks: list[dict],
        graph_weight: float,
    ) -> list[dict]:
        """按 RRF 融合基础召回与图召回的排名。"""
        fused: dict[str, dict[str, Any]] = {}
        rrf_k = 60.0

        def merge_chunk(chunk: dict, rank: int, weight: float, source: str) -> None:
            chunk_id = chunk.get("metadata", {}).get("chunk_id")
            if not chunk_id:
                return
            score = weight / (rrf_k + rank)
            existing = fused.get(chunk_id)
            if existing is None:
                existing = {**chunk, "fusion_score": 0.0, "fusion_sources": []}
                fused[chunk_id] = existing
            existing["fusion_score"] += score
            existing["score"] = existing["fusion_score"]
            existing["score_type"] = "fusion"
            existing["fusion_sources"].append(source)
            if source == "graph" and "graph_score" in chunk:
                existing["graph_score"] = chunk["graph_score"]

        for rank, chunk in enumerate(base_chunks, start=1):
            merge_chunk(chunk, rank, 1.0, "chunk")
        for rank, chunk in enumerate(graph_chunks, start=1):
            merge_chunk(chunk, rank, max(graph_weight, 0.0), "graph")

        return sorted(fused.values(), key=lambda item: item.get("fusion_score", 0.0), reverse=True)

    @staticmethod
    def _child_collection_name(embedding_dimension: int) -> str:
        """根据正整数向量维度返回 Parent-Child 集合名。"""
        if isinstance(embedding_dimension, bool) or not isinstance(embedding_dimension, int):
            raise ValueError("Parent-Child embedding dimension must be an integer")
        if embedding_dimension <= 0:
            raise ValueError("Parent-Child embedding dimension must be positive")
        return f"{CHILD_COLLECTION_PREFIX}{embedding_dimension}"

    @staticmethod
    def _child_field_map(collection: Collection) -> dict[str, Any]:
        """返回 child collection 的字段映射，供 schema 校验与调用方使用。"""
        return {field.name: field for field in collection.schema.fields}

    @classmethod
    def _validate_child_collection_schema(
        cls,
        collection: Collection,
        embedding_dimension: int,
        *,
        sparse_enabled: bool,
    ) -> None:
        """校验 child collection 的字段、维度和 BM25 函数，不匹配时拒绝继续写入。"""
        fields_by_name = cls._child_field_map(collection)
        required_types = {
            "id": DataType.VARCHAR,
            CHILD_KB_FIELD: DataType.VARCHAR,
            "file_id": DataType.VARCHAR,
            "doc_id": DataType.VARCHAR,
            "version_id": DataType.VARCHAR,
            "child_id": DataType.VARCHAR,
            "parent_id": DataType.VARCHAR,
            CHILD_TEXT_FIELD: DataType.VARCHAR,
            "chunk_index": DataType.INT64,
            "meta_info": DataType.JSON,
            CHILD_DENSE_FIELD: DataType.FLOAT_VECTOR,
            CONTENT_SPARSE_FIELD: DataType.SPARSE_FLOAT_VECTOR,
        }
        for field_name, expected_type in required_types.items():
            field = fields_by_name.get(field_name)
            if field is None or field.dtype != expected_type:
                raise ValueError(f"Child collection schema missing or invalid field: {field_name}")

        dense_field = fields_by_name[CHILD_DENSE_FIELD]
        if int(dense_field.params.get("dim", 0)) != embedding_dimension:
            raise ValueError(f"Child collection dimension mismatch: expected {embedding_dimension}, found {dense_field.params.get('dim')}")
        text_field = fields_by_name[CHILD_TEXT_FIELD]
        if text_field.params.get("enable_analyzer") is not True:
            raise ValueError("Child collection child_text must enable analyzer for BM25")

        sparse_field = fields_by_name.get(CHILD_SPARSE_FIELD)
        if sparse_enabled:
            if sparse_field is None or sparse_field.dtype != DataType.SPARSE_FLOAT_VECTOR:
                raise ValueError("BGE-M3 sparse retrieval requires bge_m3_sparse_vector in child collection")

        functions = getattr(collection.schema, "functions", ())
        if not any(
            function.type == FunctionType.BM25
            and function.input_field_names == [CHILD_TEXT_FIELD]
            and function.output_field_names == [CONTENT_SPARSE_FIELD]
            for function in functions
        ):
            raise ValueError("Child collection is missing the child_text BM25 function")

    def _create_new_child_collection(
        self,
        embedding_dimension: int,
        *,
        sparse_enabled: bool,
    ) -> Collection:
        """创建按向量维度共享的 Parent-Child 子块集合及其索引。"""
        collection_name = self._child_collection_name(embedding_dimension)
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(name=CHILD_KB_FIELD, dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="version_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="child_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="parent_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(
                name=CHILD_TEXT_FIELD,
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                enable_match=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
            ),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="meta_info", dtype=DataType.JSON),
            FieldSchema(name=CHILD_DENSE_FIELD, dtype=DataType.FLOAT_VECTOR, dim=embedding_dimension),
        ]
        # 稀疏向量字段始终存在，避免共享集合的 schema 随首个调用变化。
        fields.append(FieldSchema(name=CHILD_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR))
        fields.append(FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR))
        bm25_function = Function(
            name="child_text_bm25",
            input_field_names=[CHILD_TEXT_FIELD],
            output_field_names=[CONTENT_SPARSE_FIELD],
            function_type=FunctionType.BM25,
        )
        schema = CollectionSchema(
            fields=fields,
            description=f"Parent-Child child collection dim={embedding_dimension}",
            functions=[bm25_function],
        )
        collection = Collection(name=collection_name, schema=schema, using=self.connection_alias)
        collection.create_index(
            CHILD_DENSE_FIELD,
            {"metric_type": "IP", "index_type": "IVF_FLAT", "params": {"nlist": 1024}},
        )
        collection.create_index(
            CHILD_SPARSE_FIELD,
            {
                "metric_type": "IP",
                "index_type": "SPARSE_INVERTED_INDEX",
                "params": {"inverted_index_algo": "DAAT_MAXSCORE"},
            },
        )
        collection.create_index(
            CONTENT_SPARSE_FIELD,
            {
                "metric_type": "BM25",
                "index_type": "SPARSE_INVERTED_INDEX",
                "params": {"inverted_index_algo": "DAAT_MAXSCORE"},
            },
        )
        self._validate_child_collection_schema(collection, embedding_dimension, sparse_enabled=sparse_enabled)
        return collection

    async def _get_or_create_child_collection(
        self,
        embedding_model_spec: str | None,
        *,
        sparse_enabled: bool = False,
    ) -> Collection:
        """按 embedding 模型维度获取或创建 child collection，并拒绝不兼容 schema。"""
        if not embedding_model_spec:
            raise ValueError("Parent-Child embedding model spec is required")
        if sparse_enabled and not is_bge_m3_embedding_model_spec(embedding_model_spec):
            raise ValueError("Only BGE-M3 embedding models can enable bge_m3_sparse_enabled")
        embedding_info = model_cache.get_model_info(embedding_model_spec)
        if not embedding_info or embedding_info.model_type != "embedding":
            raise ValueError(f"Unsupported embedding model: {embedding_model_spec}")
        dimension = embedding_info.dimension
        collection_name = self._child_collection_name(dimension)
        child_collections = getattr(self, "child_collections", None)
        if child_collections is None:
            child_collections = self.child_collections = {}
        cached = child_collections.get(dimension)
        if cached is not None:
            self._validate_child_collection_schema(cached, dimension, sparse_enabled=sparse_enabled)
            return cached

        if utility.has_collection(collection_name, using=self.connection_alias):
            collection = Collection(name=collection_name, using=self.connection_alias)
            self._validate_child_collection_schema(collection, dimension, sparse_enabled=sparse_enabled)
        else:
            collection = await run_sync_with_deferred_cancellation(
                self._create_new_child_collection,
                dimension,
                sparse_enabled=sparse_enabled,
            )
        await self._initialize_kb_instance(collection)
        child_collections[dimension] = collection
        return collection

    def _get_existing_child_collection(self, embedding_dimension: int) -> Collection | None:
        """读取已有 child collection，不因删除操作隐式创建集合。"""
        collection_name = self._child_collection_name(embedding_dimension)
        child_collections = getattr(self, "child_collections", None)
        if child_collections is None:
            child_collections = self.child_collections = {}
        cached = child_collections.get(embedding_dimension)
        if cached is not None:
            return cached
        if not utility.has_collection(collection_name, using=self.connection_alias):
            return None
        collection = Collection(name=collection_name, using=self.connection_alias)
        child_collections[embedding_dimension] = collection
        return collection

    async def _get_existing_child_collection_for_query(
        self,
        embedding_dimension: int,
        *,
        sparse_enabled: bool,
    ) -> Collection | None:
        """按持久维度打开并校验已有查询集合，禁止查询路径隐式创建。"""
        child_collections = getattr(self, "child_collections", {})
        was_cached = embedding_dimension in child_collections
        collection = self._get_existing_child_collection(embedding_dimension)
        if collection is None:
            return None
        self._validate_child_collection_schema(
            collection,
            embedding_dimension,
            sparse_enabled=sparse_enabled,
        )
        if not was_cached:
            await self._initialize_kb_instance(collection)
        return collection

    @staticmethod
    def _validate_child_sparse_vector(vector: Any) -> dict[int, float]:
        """校验并复制单条 BGE-M3 稀疏向量，拒绝非法键和值。"""
        if not isinstance(vector, dict) or not vector:
            raise ValueError("BGE-M3 sparse vector must be a non-empty mapping")
        normalized: dict[int, float] = {}
        for key, value in vector.items():
            if isinstance(key, bool) or not isinstance(key, int) or key < 0:
                raise ValueError("BGE-M3 sparse vector indexes must be non-negative integers")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("BGE-M3 sparse vector values must be finite numbers")
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError("BGE-M3 sparse vector values must be finite numbers")
            normalized[key] = numeric_value
        return normalized

    @classmethod
    def _build_child_entities(
        cls,
        kb_id: str,
        children: list[dict[str, Any]],
        dense_embeddings: list[list[float]],
        sparse_embeddings: list[dict[int, float]] | None = None,
    ) -> list[list[Any]]:
        """把子块记录转换为 child collection 的列式写入实体并执行边界校验。"""
        if len(children) != len(dense_embeddings):
            raise ValueError("Child records and dense embeddings must have the same length")
        if sparse_embeddings is not None and len(sparse_embeddings) != len(children):
            raise ValueError("Child records and sparse embeddings must have the same length")
        rows: list[dict[str, Any]] = []
        for index, (child, dense) in enumerate(zip(children, dense_embeddings, strict=True)):
            child_id = child.get("child_id")
            parent_id = child.get("parent_id")
            if not isinstance(child_id, str) or not child_id:
                raise ValueError(f"Child record {index} is missing child_id")
            if not isinstance(parent_id, str) or not parent_id:
                raise ValueError(f"Child record {child_id} is missing parent_id")
            child_text = child.get("child_text", child.get("content"))
            if not isinstance(child_text, str) or not child_text:
                raise ValueError(f"Child record {child_id} is missing child_text")
            if not isinstance(dense, list) or not dense:
                raise ValueError(f"Child record {child_id} has an invalid dense vector")
            rows.append(
                {
                    "id": child_id,
                    CHILD_KB_FIELD: kb_id,
                    "file_id": str(child.get("file_id") or ""),
                    "doc_id": str(child.get("doc_id") or ""),
                    "version_id": str(child.get("version_id") or ""),
                    "child_id": child_id,
                    "parent_id": parent_id,
                    CHILD_TEXT_FIELD: child_text,
                    "chunk_index": int(child.get("child_index", child.get("chunk_index", index))),
                    "meta_info": dict(child.get("metadata") or child.get("meta_info") or {}),
                    CHILD_DENSE_FIELD: dense,
                }
            )
        columns = [[row[field] for row in rows] for field in rows[0]] if rows else []
        if sparse_embeddings is not None and rows:
            columns.append([cls._validate_child_sparse_vector(vector) for vector in sparse_embeddings])
        return columns

    @staticmethod
    def _child_scope_expr(kb_id: str, file_id: str | None = None) -> str:
        """构造 child collection 的知识库隔离过滤表达式。"""
        escaped_kb_id = str(kb_id).replace('"', '\\"')
        expression = f'{CHILD_KB_FIELD} == "{escaped_kb_id}"'
        if file_id is not None:
            escaped_file_id = str(file_id).replace('"', '\\"')
            expression += f' and file_id == "{escaped_file_id}"'
        return expression

    async def _insert_child_chunks_to_milvus(
        self,
        kb_id: str,
        collection: Collection,
        children: list[dict[str, Any]],
        dense_embeddings: list[list[float]],
        *,
        sparse_embeddings: list[dict[int, float]] | None = None,
    ) -> None:
        """仅把带父块引用的子块写入 Milvus child collection。"""
        if not children:
            return
        fields_by_name = self._child_field_map(collection)
        dense_field = fields_by_name.get(CHILD_DENSE_FIELD)
        expected_dimension = int((dense_field.params if dense_field is not None else {}).get("dim", 0))
        if expected_dimension <= 0 or any(len(vector) != expected_dimension for vector in dense_embeddings):
            raise ValueError(f"Dense embeddings must have dimension {expected_dimension}")
        has_sparse_field = CHILD_SPARSE_FIELD in fields_by_name
        if sparse_embeddings is not None and not has_sparse_field:
            raise ValueError("Sparse embeddings were provided to a child collection without sparse field")
        entities = self._build_child_entities(kb_id, children, dense_embeddings, sparse_embeddings)
        if sparse_embeddings is None and has_sparse_field:
            # Milvus 2.5 不允许 nullable 向量；空映射满足固定 schema，但不伪造稀疏坐标。
            entities.append([{} for _ in children])
        await run_sync_with_deferred_cancellation(collection.insert, entities)

    async def _delete_file_child_chunks_from_milvus(
        self,
        collection: Collection,
        kb_id: str,
        file_id: str,
    ) -> None:
        """按知识库和文件删除 child collection 中的子块，不影响其他知识库。"""
        expr = self._child_scope_expr(kb_id, file_id)
        await run_sync_with_deferred_cancellation(collection.delete, expr)

    def _list_existing_child_collections(self) -> list[Collection]:
        """枚举已存在的共享 child collection，不因清理动作创建集合。"""
        names = set(getattr(self, "child_collections", {}).keys())
        collections_by_name: dict[str, Collection] = {}
        for dimension in names:
            collection = self._get_existing_child_collection(dimension)
            if collection is not None:
                collections_by_name[collection.name] = collection
        child_collection_names = utility.list_collections(using=self.connection_alias)
        for name in child_collection_names:
            if not name.startswith(CHILD_COLLECTION_PREFIX):
                continue
            if name in collections_by_name:
                continue
            collection = Collection(name=name, using=self.connection_alias)
            collections_by_name[name] = collection
        return list(collections_by_name.values())

    async def _delete_file_child_chunks_from_all_collections(self, kb_id: str, file_id: str) -> None:
        """从所有按维度共享的 child collection 删除指定文件投影。"""
        for collection in self._list_existing_child_collections():
            await self._delete_file_child_chunks_from_milvus(collection, kb_id, file_id)

    async def _delete_kb_child_chunks_from_all_collections(self, kb_id: str) -> None:
        """从所有按维度共享的 child collection 删除指定知识库投影。"""
        for collection in self._list_existing_child_collections():
            escaped_kb_id = str(kb_id).replace('"', '\\"')
            await run_sync_with_deferred_cancellation(
                collection.delete,
                f'{CHILD_KB_FIELD} == "{escaped_kb_id}"',
            )

    async def _delete_child_version_from_milvus(
        self,
        collection: Collection,
        kb_id: str,
        version_id: str,
    ) -> None:
        """按知识库和版本删除尚未激活的子块，避免影响旧 active 版本。"""
        escaped_kb_id = str(kb_id).replace('"', '\\"')
        escaped_version_id = str(version_id).replace('"', '\\"')
        expression = f'knowledge_base_id == "{escaped_kb_id}" and version_id == "{escaped_version_id}"'
        await run_sync_with_deferred_cancellation(collection.delete, expression)

    async def cleanup_superseded_parent_child_version(
        self,
        kb_id: str,
        file_id: str,
        version_id: str,
        *,
        control_check: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """分阶段校验 owner 后幂等清理 superseded 版本的外部投影和引用。"""
        repository = KnowledgeParentChildChunkRepository()
        version = await repository.get_version(version_id)
        if version is None:
            return {"kb_id": kb_id, "file_id": file_id, "version_id": version_id, "status": "already_clean"}
        if str(version.kb_id) != str(kb_id) or str(version.file_id) != str(file_id):
            raise ValueError("Parent-Child cleanup target does not match its persisted owner")
        if version.status != "superseded":
            raise ValueError("Only superseded Parent-Child versions can be cleaned")

        from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

        cleanup_errors: list[str] = []
        if control_check is not None:
            await control_check()
        try:
            await KnowledgeGraphService().delete_parent_child_version_graph(kb_id, file_id, version_id)
        except Exception as exc:
            cleanup_errors.append(f"graph={type(exc).__name__}: {exc}")

        if control_check is not None:
            await control_check()
        try:
            collection = self._get_existing_child_collection(int(version.embedding_dimension))
            if collection is not None:
                if control_check is not None:
                    await control_check()
                await self._delete_child_version_from_milvus(collection, kb_id, version_id)
                if control_check is not None:
                    await control_check()
        except Exception as exc:
            cleanup_errors.append(f"milvus={type(exc).__name__}: {exc}")

        if cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))

        if control_check is not None:
            await control_check()
        await repository.delete_version(version_id)
        if control_check is not None:
            await control_check()
        await invalidate_parent_version(kb_id, version_id)
        return {"kb_id": kb_id, "file_id": file_id, "version_id": version_id, "status": "cleaned"}

    async def _finish_parent_child_activation(
        self,
        kb_id: str,
        file_id: str,
        active_version: Any | None,
        *,
        control_check: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """发布后失效缓存；旧投影由同事务登记的代次 outbox 唯一回收。"""
        await invalidate_query_cache(kb_id)
        if active_version is not None:
            await invalidate_parent_version(kb_id, active_version.version_id)

    async def _index_parent_child_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        file_meta: dict[str, Any],
        params: dict[str, Any],
        embedding_model_spec: str,
        embedding_function,
        processing_job_id: str,
        processing_owner: str,
        operator_id: str | None = None,
        markdown_content: str | None = None,
    ) -> dict:
        """以 staging -> Milvus -> flush -> active 顺序写入 Parent-Child 版本。"""
        model_info = model_cache.get_model_info(embedding_model_spec)
        dimension = int(getattr(model_info, "dimension", 0) or 0)
        if dimension <= 0:
            raise ValueError("Parent-Child embedding model must expose a positive dimension")

        repository = KnowledgeParentChildChunkRepository()
        active_version = await repository.get_active_version(kb_id, file_id)
        doc_id = f"doc_{uuid.uuid4().hex}"
        version = await repository.create_staging_version(
            kb_id=kb_id,
            file_id=file_id,
            doc_id=doc_id,
            params=params,
            embedding_model_spec=embedding_model_spec,
            embedding_dimension=dimension,
        )
        version_id = version.version_id
        child_collection: Collection | None = None
        inserted = False
        activation_committed = False
        committed_result: dict[str, Any] | None = None
        try:
            child_collection = await self._get_or_create_child_collection(
                embedding_model_spec,
                sparse_enabled=(params.get("embedding_features") or {}).get("bge_m3_sparse_enabled") is True,
            )
            sparse_enabled = (params.get("embedding_features") or {}).get("bge_m3_sparse_enabled") is True
            if markdown_content is None:
                markdown_content = await documents.read_markdown_from_minio(file_meta["markdown_file"])
            chunked = chunk_markdown_parent_child(
                markdown_content,
                file_id,
                kb_id,
                version_id,
                params,
                filename=str(file_meta.get("filename") or ""),
            )
            parents = [{**parent, "file_id": file_id, "doc_id": doc_id} for parent in chunked["parents"]]
            children = [
                {
                    **child,
                    "file_id": file_id,
                    "doc_id": doc_id,
                    "version_id": version_id,
                    # repository 在版本内以 child_index 唯一，转换为全局顺序避免跨父块冲突。
                    "child_index": child_index,
                }
                for child_index, child in enumerate(chunked["children"])
            ]
            await repository.batch_insert_parent_chunks(version_id, parents)
            await repository.batch_insert_child_chunks(version_id, children)

            if children:
                child_texts = [child["child_text"] for child in children]
                sparse_embeddings = None
                if sparse_enabled:
                    sparse_encoder = getattr(embedding_function, "abatch_encode_with_sparse", None)
                    model = getattr(embedding_function, "__self__", None) or getattr(
                        getattr(embedding_function, "func", None), "__self__", None
                    )
                    sparse_encoder = sparse_encoder or getattr(model, "abatch_encode_with_sparse", None)
                    if not callable(sparse_encoder):
                        raise ValueError("BGE-M3 sparse 已启用，但 embedding provider 未提供 sparse 输出")
                    embeddings, sparse_embeddings = await sparse_encoder(child_texts)
                else:
                    embeddings = await embedding_function(child_texts)
                # insert 可能在服务端部分成功，调用前即标记以确保异常路径也执行按版本清理。
                inserted = True
                await self._insert_child_chunks_to_milvus(
                    kb_id,
                    child_collection,
                    children,
                    embeddings,
                    sparse_embeddings=sparse_embeddings,
                )
                await run_sync_with_deferred_cancellation(child_collection.flush)
                escaped_version_id = version_id.replace('"', '\\"')
                rows = await asyncio.to_thread(
                    child_collection.query,
                    expr=f'version_id == "{escaped_version_id}"',
                    output_fields=["child_id"],
                    limit=max(len(children), 1),
                )
                if len(rows) != len(children):
                    raise RuntimeError("Milvus child write could not be read back after flush")

            chunk_count = len(children)
            token_count = sum(int(parent.get("token_count") or 0) for parent in parents)
            activation, cancelled_during_activation = await _await_with_deferred_cancellation(
                repository.activate_version(
                    version_id,
                    processing_job_id=processing_job_id,
                    processing_owner=processing_owner,
                    chunk_count=chunk_count,
                    token_count=token_count,
                    updated_by=operator_id,
                )
            )
            activation_committed = True
            _activated_version, activated_file = activation
            committed_result = documents.file_record_to_meta(activated_file)

            async def assert_task_control() -> None:
                """确认直接索引 attempt 仍持有有效的 Durable Task lease。"""
                owns_lease, cancel_requested = await BackgroundJobRepository().check_control(
                    processing_job_id,
                    worker_id=processing_owner,
                )
                if not owns_lease:
                    raise asyncio.CancelledError("Task lease was lost")
                if cancel_requested:
                    raise asyncio.CancelledError("Task was cancelled")

            try:
                _, cancelled_during_finalization = await _await_with_deferred_cancellation(
                    self._finish_parent_child_activation(
                        kb_id,
                        file_id,
                        active_version,
                        control_check=assert_task_control,
                    )
                )
            except (Exception, asyncio.CancelledError) as exc:
                raise _CommittedParentChildIndex(committed_result, exc) from exc
            if cancelled_during_activation or cancelled_during_finalization:
                cancellation = asyncio.CancelledError("File indexing was cancelled after Parent-Child activation")
                raise _CommittedParentChildIndex(committed_result, cancellation) from cancellation
            return committed_result
        except _CommittedParentChildIndex:
            raise
        except (Exception, asyncio.CancelledError) as exc:
            # 激活提交后 PostgreSQL 已切换事实版本，新 Milvus 投影不能再按 staging 回滚。
            if activation_committed and committed_result is not None:
                raise _CommittedParentChildIndex(committed_result, exc) from exc
            projection_deleted = True
            if inserted and child_collection is not None:
                try:
                    await self._delete_child_version_from_milvus(child_collection, kb_id, version_id)
                except Exception as cleanup_error:
                    projection_deleted = False
                    logger.error(f"Failed to rollback Milvus child version {version_id}: {cleanup_error}")
            # 外部删除失败保留版本归属，下一次 begin_index_generation 的 outbox 可准确重试。
            if projection_deleted:
                try:
                    await repository.delete_version(version_id)
                except Exception as cleanup_error:
                    logger.error(f"Failed to rollback PostgreSQL child version {version_id}: {cleanup_error}")
            raise

    @staticmethod
    def _validate_single_collection_sparse_schema(collection: Collection) -> None:
        """确认单层集合具备真实模型 sparse 字段。"""
        fields = {field.name: field for field in collection.schema.fields}
        sparse_field = fields.get(CHILD_SPARSE_FIELD)
        if sparse_field is None or sparse_field.dtype != DataType.SPARSE_FLOAT_VECTOR:
            raise ValueError("BGE-M3 sparse retrieval requires bge_m3_sparse_vector in the Milvus collection")

    async def _aquery_single_features(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        collection: Collection,
        merged_kwargs: dict[str, Any],
    ) -> list[dict]:
        """执行单层 chunk 的模型 sparse 融合或全链路 RRF。"""
        embedding_model_spec = config.embedding_model_spec
        final_top_k = max(int(merged_kwargs.get("final_top_k", 10)), 1)
        similarity_threshold = float(merged_kwargs.get("similarity_threshold", 0.2))
        include_distances = bool(merged_kwargs.get("include_distances", True))
        search_mode = str(merged_kwargs.get("search_mode", "vector")).lower()
        if search_mode not in {"vector", "keyword", "hybrid"}:
            search_mode = "vector"
        use_vector_fusion = bool(merged_kwargs.get("use_vector_score_fusion", False))
        use_rrf = bool(merged_kwargs.get("use_rrf", False))
        use_reranker = bool(merged_kwargs.get("use_reranker", False))
        use_graph_retrieval = bool(merged_kwargs.get("use_graph_retrieval", False))
        recall_top_k = final_top_k
        if use_reranker or use_graph_retrieval:
            recall_top_k = max(int(merged_kwargs.get("recall_top_k", 50)), final_top_k)

        if use_vector_fusion:
            sparse_enabled = (config.additional_params.get("embedding_features") or {}).get("bge_m3_sparse_enabled") is True
            if not sparse_enabled:
                raise ValueError("use_vector_score_fusion requires bge_m3_sparse_enabled")
            self._validate_single_collection_sparse_schema(collection)

        active_expr = await self._build_active_generation_expr(kb_id)
        file_filter = await self._build_file_name_expr(kb_id, merged_kwargs.get("file_name"))
        text_filter, _highlighter = self._build_text_search(merged_kwargs, search_mode, query_text)
        file_expr = " and ".join(f"({part})" for part in (active_expr, file_filter, text_filter) if part)
        output_fields = ["content", "chunk_id", "file_id", "generation", "chunk_index"]
        dense_candidates: list[dict[str, Any]] = []
        sparse_candidates: list[dict[str, Any]] = []
        bm25_candidates: list[dict[str, Any]] = []

        if search_mode in {"vector", "hybrid"}:
            if not embedding_model_spec:
                raise ValueError("Vector query requires an embedding model")
            embedding_function = get_embedding_function(embedding_model_spec, sync=True)
            query_embedding = await run_milvus_query_io(embedding_function, [query_text])
            dense_results = await run_milvus_query_io(
                collection.search,
                data=query_embedding,
                anns_field="embedding",
                param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
                limit=recall_top_k,
                expr=file_expr,
                output_fields=output_fields,
            )
            dense_candidates = [
                self._build_chunk_from_hit(hit, hit.distance, include_distances, score_field="dense_score")
                for hit in (dense_results[0] if dense_results else [])
            ]
            if use_vector_fusion:
                sparse_vector = await self._get_sparse_query_vector(embedding_model_spec, query_text)
                sparse_results = await run_milvus_query_io(
                    collection.search,
                    data=[sparse_vector],
                    anns_field=CHILD_SPARSE_FIELD,
                    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.0}},
                    limit=recall_top_k,
                    expr=file_expr,
                    output_fields=output_fields,
                )
                sparse_candidates = [
                    self._build_chunk_from_hit(hit, hit.distance, include_distances, score_field="sparse_score")
                    for hit in (sparse_results[0] if sparse_results else [])
                ]

        if search_mode in {"keyword", "hybrid"}:
            bm25_results = await run_milvus_query_io(
                collection.search,
                data=[query_text],
                anns_field=CONTENT_SPARSE_FIELD,
                param={
                    "metric_type": "BM25",
                    "params": {"drop_ratio_search": float(merged_kwargs.get("bm25_drop_ratio_search", 0.0))},
                },
                limit=max(int(merged_kwargs.get("bm25_top_k", recall_top_k)), 1),
                expr=file_expr,
                output_fields=output_fields,
            )
            bm25_candidates = [
                self._build_chunk_from_hit(hit, hit.distance, include_distances, score_field="bm25_score")
                for hit in (bm25_results[0] if bm25_results else [])
            ]

        if use_vector_fusion and (dense_candidates or sparse_candidates):
            vector_candidates = self._merge_vector_candidates(
                dense_candidates,
                sparse_candidates,
                float(merged_kwargs.get("dense_vector_weight", 0.7)),
                float(merged_kwargs.get("sparse_vector_weight", 0.3)),
                identity_key="chunk_id",
            )
        else:
            vector_candidates = dense_candidates

        graph_candidates: list[dict[str, Any]] = []
        if use_graph_retrieval:
            graph_candidates = await self._retrieve_graph_chunks(
                query_text,
                kb_id,
                vector_candidates + bm25_candidates,
                merged_kwargs,
                embedding_model_spec,
            )

        if use_rrf:
            candidate_lists = []
            if search_mode in {"vector", "hybrid"}:
                candidate_lists.append(
                    [candidate for candidate in vector_candidates if float(candidate.get("score") or 0.0) >= similarity_threshold]
                )
            if search_mode in {"keyword", "hybrid"}:
                candidate_lists.append(
                    [candidate for candidate in bm25_candidates if float(candidate.get("score") or 0.0) >= similarity_threshold]
                )
            if graph_candidates:
                candidate_lists.append(
                    [candidate for candidate in graph_candidates if float(candidate.get("score") or 0.0) >= similarity_threshold]
                )
            retrieved_chunks = self._rrf_candidates(candidate_lists, identity_key="chunk_id")
        elif search_mode == "hybrid":
            retrieved_chunks = self._merge_ranked_candidates(
                [vector_candidates, bm25_candidates],
                [
                    float(merged_kwargs.get("vector_weight", 0.7)),
                    float(merged_kwargs.get("bm25_weight", 0.3)),
                ],
                identity_key="chunk_id",
            )
        elif search_mode == "keyword":
            retrieved_chunks = bm25_candidates
        else:
            retrieved_chunks = vector_candidates

        if graph_candidates and not use_rrf:
            retrieved_chunks = self._fuse_chunk_rankings(
                retrieved_chunks,
                graph_candidates,
                float(merged_kwargs.get("graph_weight", 1.0)),
            )
        if use_rrf:
            retrieved_chunks = retrieved_chunks[:recall_top_k]
        else:
            retrieved_chunks = [chunk for chunk in retrieved_chunks if float(chunk.get("score") or 0.0) >= similarity_threshold][
                :recall_top_k
            ]
        if not retrieved_chunks:
            return []

        retrieved_chunks = await self._hydrate_chunk_sources(kb_id, retrieved_chunks)
        if use_reranker:
            reranker_model = merged_kwargs.get("reranker_model")
            if not reranker_model:
                raise ValueError("Reranker model must be specified when use_reranker=True")
            from yuxi.modules.models.rerank import get_reranker

            reranker = get_reranker(reranker_model)
            try:
                scores = await reranker.acompute_score(
                    [query_text, [chunk["content"] for chunk in retrieved_chunks]],
                    normalize=True,
                )
                for chunk, score in zip(retrieved_chunks, scores, strict=False):
                    chunk["rerank_score"] = float(score)
                    chunk["score"] = float(score)
                retrieved_chunks.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
            finally:
                await reranker.aclose()
        return retrieved_chunks[:final_top_k]

    @staticmethod
    def _candidate_identity(candidate: dict[str, Any], identity_key: str) -> str:
        """从候选或其 metadata 读取稳定身份。"""
        identity = candidate.get(identity_key)
        metadata = candidate.get("metadata")
        if identity is None and isinstance(metadata, dict):
            identity = metadata.get(identity_key)
        if identity is None:
            raise ValueError(f"Retrieval candidate is missing {identity_key}")
        return str(identity)

    @classmethod
    def _min_max_normalize(
        cls,
        candidates: list[dict[str, Any]],
        *,
        identity_key: str = "child_id",
    ) -> dict[str, float]:
        """在本次候选集内按指定身份执行 Min-Max 归一化。"""
        if not candidates:
            return {}
        scores = {cls._candidate_identity(item, identity_key): float(item.get("score") or 0.0) for item in candidates}
        minimum, maximum = min(scores.values()), max(scores.values())
        if minimum == maximum:
            return {identity: 1.0 for identity in scores}
        return {identity: (score - minimum) / (maximum - minimum) for identity, score in scores.items()}

    @classmethod
    def _merge_vector_candidates(
        cls,
        dense_candidates: list[dict[str, Any]],
        sparse_candidates: list[dict[str, Any]],
        dense_weight: float,
        sparse_weight: float,
        *,
        identity_key: str = "child_id",
    ) -> list[dict[str, Any]]:
        """归一化并按原始权重融合 dense 与 BGE-M3 sparse 候选。"""
        branches = [
            ("dense", dense_candidates, float(dense_weight)),
            ("sparse", sparse_candidates, float(sparse_weight)),
        ]
        branches = [(name, items, weight) for name, items, weight in branches if items and weight > 0]
        if not branches:
            raise ValueError("dense_vector_weight 与 sparse_vector_weight 不能同时为 0")
        total_weight = sum(weight for _, _, weight in branches)
        merged: dict[str, dict[str, Any]] = {}
        for name, candidates, weight in branches:
            normalized = cls._min_max_normalize(candidates, identity_key=identity_key)
            for candidate in candidates:
                identity = cls._candidate_identity(candidate, identity_key)
                result = merged.setdefault(identity, dict(candidate))
                if result.get("_fusion_initialized") is not True:
                    result["score"] = 0.0
                    result["_fusion_initialized"] = True
                result["score"] = float(result.get("score") or 0.0) + weight / total_weight * normalized[identity]
                result[f"{name}_score"] = float(candidate.get("score") or 0.0)
                for key, value in candidate.items():
                    if value is not None and result.get(key) is None:
                        result[key] = value
        for result in merged.values():
            result["vector_score"] = result["score"]
            result.pop("_fusion_initialized", None)
        return sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)

    @classmethod
    def _merge_ranked_candidates(
        cls,
        lists: list[list[dict[str, Any]]],
        weights: list[float],
        *,
        identity_key: str = "child_id",
    ) -> list[dict[str, Any]]:
        """按 Min-Max 分数和归一化权重合并多路 child 候选。"""
        branches = [(items, float(weights[index])) for index, items in enumerate(lists) if items and weights[index] > 0]
        if not branches:
            return []
        total = sum(weight for _, weight in branches)
        merged: dict[str, dict[str, Any]] = {}
        for candidates, weight in branches:
            normalized = cls._min_max_normalize(candidates, identity_key=identity_key)
            for candidate in candidates:
                identity = cls._candidate_identity(candidate, identity_key)
                result = merged.setdefault(identity, dict(candidate))
                if result.get("_fusion_initialized") is not True:
                    result["score"] = 0.0
                    result["_fusion_initialized"] = True
                result["score"] = float(result.get("score") or 0.0) + weight / total * normalized[identity]
                for key, value in candidate.items():
                    if value is not None and result.get(key) is None:
                        result[key] = value
        for result in merged.values():
            result.pop("_fusion_initialized", None)
        return sorted(merged.values(), key=lambda item: float(item.get("score") or 0.0), reverse=True)

    @classmethod
    def _rrf_candidates(
        cls,
        lists: list[list[dict[str, Any]]],
        *,
        identity_key: str = "child_id",
    ) -> list[dict[str, Any]]:
        """以固定常数 60 在指定候选身份粒度执行 RRF。"""
        fused: dict[str, dict[str, Any]] = {}
        for candidates in lists:
            for rank, candidate in enumerate(candidates, start=1):
                identity = cls._candidate_identity(candidate, identity_key)
                result = fused.setdefault(identity, dict(candidate))
                result["rrf_score"] = float(result.get("rrf_score") or 0.0) + 1.0 / (60.0 + rank)
                result["score"] = result["rrf_score"]
                for key, value in candidate.items():
                    if value is not None and result.get(key) is None:
                        result[key] = value
        return sorted(fused.values(), key=lambda item: float(item.get("score") or 0.0), reverse=True)

    @staticmethod
    def _child_candidate_from_hit(hit: Any, score: float, *, score_field: str | None = None) -> dict[str, Any]:
        """将 child collection 的 Milvus hit 转为可融合的子块候选。"""
        entity = hit.entity
        child_id = entity.get("child_id") or entity.get("id")
        parent_id = entity.get("parent_id")
        if not child_id or not parent_id:
            raise ValueError("Parent-Child Milvus hit 缺少 child_id 或 parent_id")
        metadata = entity.get("meta_info") or {}
        candidate = {
            "child_id": str(child_id),
            "parent_id": str(parent_id),
            "child_text": entity.get(CHILD_TEXT_FIELD, ""),
            "score": float(score or 0.0),
            "file_id": entity.get("file_id"),
            "doc_id": entity.get("doc_id"),
            "version_id": entity.get("version_id"),
            "chunk_index": entity.get("chunk_index"),
            "meta_info": dict(metadata) if isinstance(metadata, dict) else {},
        }
        if score_field:
            candidate[score_field] = float(score or 0.0)
        return candidate

    async def _get_sparse_query_vector(self, embedding_model_spec: str, query_text: str) -> dict[int, float]:
        """从 embedding provider 获取 sparse 查询向量，缺少能力时拒绝查询。"""
        from yuxi.modules.models.embed import select_embedding_model

        model = select_embedding_model(embedding_model_spec)
        with_sparse = getattr(model, "aencode_with_sparse", None)
        if callable(with_sparse):
            _dense, sparse = await with_sparse([query_text])
            if not sparse:
                raise ValueError("BGE-M3 sparse provider returned no query vector")
            return self._validate_child_sparse_vector(sparse[0])
        method = next(
            (
                getattr(model, name, None)
                for name in (
                    "aencode_sparse",
                    "encode_sparse",
                    "abatch_encode_sparse",
                    "batch_encode_sparse",
                    "aencode_with_sparse",
                    "encode_with_sparse",
                )
                if callable(getattr(model, name, None))
            ),
            None,
        )
        if method is None:
            raise ValueError("BGE-M3 sparse 已启用，但 embedding provider 未提供 sparse 输出")
        result = method([query_text]) if "batch" in getattr(method, "__name__", "") else method(query_text)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, tuple) and len(result) == 2:
            result = result[1]
        if isinstance(result, list):
            result = result[0] if result else None
        return self._validate_child_sparse_vector(result)

    async def _retrieve_parent_child_graph_candidates(
        self,
        query_text: str,
        kb_id: str,
        base_candidates: list[dict[str, Any]],
        query_params: dict[str, Any],
        embedding_model_spec: str | None,
        active_version_ids: list[str],
        repository: KnowledgeParentChildChunkRepository,
    ) -> list[dict[str, Any]]:
        """从 ParentChunk 图谱 PPR 显式展开 active child 候选。"""
        try:
            from yuxi.modules.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
            from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

            if not embedding_model_spec:
                return []
            entity_top_k = max(int(query_params.get("graph_entity_top_k", 10)), 1)
            triple_top_k = max(int(query_params.get("graph_triple_top_k", 10)), 1)
            graph_top_k = max(int(query_params.get("graph_top_k", 20)), 1)
            graph_max_nodes = max(int(query_params.get("graph_max_nodes", 10000)), 1)
            vector_store = await run_milvus_query_io(MilvusGraphVectorStore)
            entity_hits, triple_hits = await asyncio.gather(
                vector_store.search_entities(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=entity_top_k,
                ),
                vector_store.search_triples(
                    kb_id=kb_id,
                    query_text=query_text,
                    embedding_model_spec=embedding_model_spec,
                    top_k=triple_top_k,
                ),
            )
            seed_weights: dict[str, float] = {}

            def add_seed(entity_id: str | None, score: float, weight: float) -> None:
                if entity_id:
                    seed_weights[entity_id] = seed_weights.get(entity_id, 0.0) + max(float(score or 0.0), 0.0) * weight

            for hit in entity_hits:
                add_seed(hit.get("id"), hit.get("score", 0.0), 1.0)
            for hit in triple_hits:
                score = float(hit.get("score") or 0.0)
                add_seed(hit.get("source_id"), score, 0.8)
                add_seed(hit.get("target_id"), score, 0.8)

            parent_scores: dict[str, float] = {}
            for candidate in base_candidates:
                parent_id = str(candidate.get("parent_id") or "")
                if parent_id:
                    parent_scores[parent_id] = max(
                        parent_scores.get(parent_id, 0.0),
                        float(candidate.get("score") or 0.0),
                    )
            if parent_scores:
                parent_records = await repository.list_parents_by_ids(list(parent_scores))
                active_versions = {str(version_id) for version_id in active_version_ids}
                for parent in parent_records:
                    if str(getattr(parent, "kb_id", "")) != str(kb_id) or str(getattr(parent, "version_id", "")) not in active_versions:
                        continue
                    for entity_id in getattr(parent, "ent_ids", None) or []:
                        add_seed(str(entity_id), parent_scores.get(str(parent.parent_id), 0.0), 0.3)

            total = sum(seed_weights.values())
            if total <= 0:
                return []
            seed_weights = {entity_id: value / total for entity_id, value in seed_weights.items()}
            graph_service = KnowledgeGraphService()
            graph_hits = await graph_service.query_and_rank_child_chunks_by_ppr(
                kb_id,
                seed_weights,
                version_ids=active_version_ids,
                max_nodes=graph_max_nodes,
                top_k=graph_top_k,
                damping=float(query_params.get("ppr_damping", 0.85)),
            )
            if not graph_hits:
                return []

            records = await repository.list_children_by_ids([str(candidate["child_id"]) for candidate in graph_hits])
            records_by_id = {str(record.child_id): record for record in records}
            active_versions = {str(version_id) for version_id in active_version_ids}
            candidates = []
            for graph_hit in graph_hits:
                child_id = str(graph_hit.get("child_id") or "")
                record = records_by_id.get(child_id)
                if record is None:
                    continue
                if (
                    str(record.kb_id) != str(kb_id)
                    or str(record.version_id) not in active_versions
                    or str(record.parent_id) != str(graph_hit.get("parent_id") or "")
                ):
                    continue
                metadata = dict(getattr(record, "chunk_metadata", None) or {})
                metadata.setdefault("spans", getattr(record, "spans", None) or [])
                graph_score = float(graph_hit.get("graph_score") or 0.0)
                candidates.append(
                    {
                        "child_id": child_id,
                        "parent_id": str(record.parent_id),
                        "child_text": record.child_text,
                        "score": graph_score,
                        "graph_score": graph_score,
                        "file_id": record.file_id,
                        "doc_id": record.doc_id,
                        "version_id": record.version_id,
                        "chunk_index": record.child_index,
                        "meta_info": metadata,
                        "start_offset": record.start_offset,
                        "end_offset": record.end_offset,
                    }
                )
            return candidates[:graph_top_k]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Parent-Child graph retrieval failed for {kb_id}: {exc}")
            raise RuntimeError(f"Parent-Child graph retrieval failed for {kb_id}") from exc

    async def _aquery_parent_child(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        merged_kwargs: dict[str, Any],
        agent_call: bool = False,
    ) -> list[dict]:
        """执行 Parent-Child 子块检索、融合、重排和父块回读。"""
        del agent_call
        repository = KnowledgeParentChildChunkRepository()
        active_storage_targets = await repository.list_active_storage_targets(kb_id)
        if not active_storage_targets:
            return []
        active_version_ids = [version_id for version_id, _dimension in active_storage_targets]
        active_dimensions = {dimension for _version_id, dimension in active_storage_targets}
        if len(active_dimensions) != 1:
            raise ValueError("Parent-Child active versions use multiple embedding dimensions; re-slice before querying")
        persisted_dimension = next(iter(active_dimensions))
        cache_scope = active_versions_fingerprint(active_version_ids)
        fingerprint = query_fingerprint(query_text, merged_kwargs)
        cached_results = await get_cached_query(kb_id, cache_scope, fingerprint)
        if cached_results is not None:
            return cached_results

        embedding_model_spec = config.embedding_model_spec
        search_mode = str(merged_kwargs.get("search_mode", "vector")).lower()
        if search_mode not in {"vector", "keyword", "hybrid"}:
            search_mode = "vector"
        query_embedding = None
        if search_mode in {"vector", "hybrid"}:
            if not embedding_model_spec:
                raise ValueError("Parent-Child vector query requires an embedding model")
            embedding_function = get_embedding_function(embedding_model_spec, sync=True)
            query_embedding = await run_milvus_query_io(embedding_function, [query_text])
            if len(query_embedding) != 1 or len(query_embedding[0]) != persisted_dimension:
                raise ValueError(f"Parent-Child query embedding dimension mismatch: expected {persisted_dimension}")

        sparse_enabled = (config.additional_params.get("embedding_features") or {}).get("bge_m3_sparse_enabled") is True
        collection = await self._get_existing_child_collection_for_query(
            persisted_dimension,
            sparse_enabled=sparse_enabled,
        )
        if collection is None:
            raise ValueError(f"Parent-Child collection for {kb_id} with persisted dimension {persisted_dimension} is unavailable")
        top_k_child = max(int(merged_kwargs.get("top_k_child", 30)), 1)
        top_k_parent = max(int(merged_kwargs.get("top_k_parent", merged_kwargs.get("final_top_k", 10))), 1)
        expression = self._child_scope_expr(kb_id)
        escaped_versions = ", ".join('"' + str(version_id).replace('"', '\\"') + '"' for version_id in active_version_ids)
        expression = f"{expression} and version_id in [{escaped_versions}]"
        file_expr = await self._build_file_name_expr(kb_id, merged_kwargs.get("file_name"))
        if file_expr:
            expression = f"{expression} and ({file_expr})"
        text_filter, _highlighter = self._build_text_search(merged_kwargs, search_mode, query_text)
        if text_filter:
            child_text_filter = re.sub(r"(?<=\()\s*content(?=\s*,)", CHILD_TEXT_FIELD, text_filter)
            expression = f"({expression}) and ({child_text_filter})"
        output_fields = [
            "child_id",
            "parent_id",
            CHILD_TEXT_FIELD,
            "file_id",
            "doc_id",
            "version_id",
            "chunk_index",
            "meta_info",
        ]
        recall_limit = max(top_k_child, int(merged_kwargs.get("recall_top_k", top_k_child)))
        dense_candidates: list[dict[str, Any]] = []
        sparse_candidates: list[dict[str, Any]] = []
        bm25_candidates: list[dict[str, Any]] = []

        if search_mode in {"vector", "hybrid"}:
            dense_results = await run_milvus_query_io(
                collection.search,
                data=query_embedding,
                anns_field=CHILD_DENSE_FIELD,
                param={"metric_type": "IP", "params": {"nprobe": 10}},
                limit=recall_limit,
                expr=expression,
                output_fields=output_fields,
            )
            dense_candidates = [
                self._child_candidate_from_hit(hit, hit.distance, score_field="dense_score")
                for hit in (dense_results[0] if dense_results else [])
            ]
            if bool(merged_kwargs.get("use_vector_score_fusion", False)):
                sparse_vector = await self._get_sparse_query_vector(embedding_model_spec, query_text)
                sparse_results = await run_milvus_query_io(
                    collection.search,
                    data=[sparse_vector],
                    anns_field=CHILD_SPARSE_FIELD,
                    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.0}},
                    limit=recall_limit,
                    expr=expression,
                    output_fields=output_fields,
                )
                sparse_candidates = [
                    self._child_candidate_from_hit(hit, hit.distance, score_field="sparse_score")
                    for hit in (sparse_results[0] if sparse_results else [])
                ]

        if search_mode in {"keyword", "hybrid"}:
            bm25_results = await run_milvus_query_io(
                collection.search,
                data=[query_text],
                anns_field=CONTENT_SPARSE_FIELD,
                param={
                    "metric_type": "BM25",
                    "params": {"drop_ratio_search": float(merged_kwargs.get("bm25_drop_ratio_search", 0.0))},
                },
                limit=max(int(merged_kwargs.get("bm25_top_k", recall_limit)), 1),
                expr=expression,
                output_fields=output_fields,
            )
            bm25_candidates = [
                self._child_candidate_from_hit(hit, hit.distance, score_field="bm25_score")
                for hit in (bm25_results[0] if bm25_results else [])
            ]

        if bool(merged_kwargs.get("use_vector_score_fusion", False)) and (dense_candidates or sparse_candidates):
            vector_candidates = self._merge_vector_candidates(
                dense_candidates,
                sparse_candidates,
                float(merged_kwargs.get("dense_vector_weight", 0.7)),
                float(merged_kwargs.get("sparse_vector_weight", 0.3)),
            )
        else:
            vector_candidates = dense_candidates
        graph_candidates = []
        if bool(merged_kwargs.get("use_graph_retrieval", False)):
            graph_candidates = await self._retrieve_parent_child_graph_candidates(
                query_text,
                kb_id,
                vector_candidates + bm25_candidates,
                merged_kwargs,
                embedding_model_spec,
                active_version_ids,
                repository,
            )

        candidate_lists: list[list[dict[str, Any]]] = []
        candidate_weights: list[float] = []
        if search_mode in {"vector", "hybrid"}:
            candidate_lists.append(vector_candidates)
            candidate_weights.append(float(merged_kwargs.get("vector_weight", 0.7)) if search_mode == "hybrid" else 1.0)
        if search_mode in {"keyword", "hybrid"}:
            candidate_lists.append(bm25_candidates)
            candidate_weights.append(float(merged_kwargs.get("bm25_weight", 0.3)) if search_mode == "hybrid" else 1.0)
        if graph_candidates:
            candidate_lists.append(graph_candidates)
            candidate_weights.append(float(merged_kwargs.get("graph_weight", 1.0)))
        use_rrf = bool(merged_kwargs.get("use_rrf", False))
        threshold = float(merged_kwargs.get("similarity_threshold", 0.0))
        if use_rrf:
            candidate_lists = [
                [item for item in candidate_list if float(item.get("score") or 0.0) >= threshold] for candidate_list in candidate_lists
            ]
            candidates = self._rrf_candidates(candidate_lists)
        elif len(candidate_lists) == 1:
            candidates = candidate_lists[0]
        else:
            candidates = self._merge_ranked_candidates(candidate_lists, candidate_weights)
        if use_rrf:
            candidates = candidates[:recall_limit]
        else:
            candidates = [item for item in candidates if float(item.get("score") or 0.0) >= threshold][:recall_limit]
        if not candidates:
            await cache_query(kb_id, cache_scope, fingerprint, [])
            return []

        if bool(merged_kwargs.get("use_reranker", False)):
            reranker_model = merged_kwargs.get("reranker_model")
            if not reranker_model:
                raise ValueError("Reranker model must be specified when use_reranker=True")
            from yuxi.modules.models.rerank import get_reranker

            reranker = get_reranker(reranker_model)
            try:
                scores = await reranker.acompute_score(
                    [query_text, [item.get("child_text", "") for item in candidates]],
                    normalize=True,
                )
                for item, score in zip(candidates, scores, strict=False):
                    item["rerank_score"] = float(score)
                    item["score"] = float(score)
                candidates.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
            finally:
                await reranker.aclose()

        grouped: dict[str, dict[str, Any]] = {}
        for item in candidates:
            parent_id = str(item["parent_id"])
            group = grouped.setdefault(parent_id, {"parent_id": parent_id, "score": float("-inf"), "children": []})
            group["children"].append(item)
            group["score"] = max(group["score"], float(item.get("score") or 0.0))
        parents: dict[str, Any] = {}
        missing_parent_ids = []
        for parent_id, group in grouped.items():
            version_id = str(group["children"][0].get("version_id") or "")
            cached = await get_cached_parent(kb_id, version_id, parent_id) if version_id else None
            if cached is None:
                missing_parent_ids.append(parent_id)
            else:
                parents[parent_id] = cached
        parent_records = await repository.list_parents_by_ids(missing_parent_ids)
        for record in parent_records:
            parents[str(record.parent_id)] = record
            record_version_id = str(getattr(record, "version_id", "") or "")
            if record_version_id:
                await cache_parent(
                    kb_id,
                    record_version_id,
                    str(record.parent_id),
                    {
                        "parent_id": record.parent_id,
                        "parent_text": record.parent_text,
                        "doc_id": record.doc_id,
                        "file_id": record.file_id,
                        "version_id": record_version_id,
                        "parent_index": getattr(record, "parent_index", None),
                    },
                )
        # child collection 已携带 spans 与 chunk_index；父块正文只从 PostgreSQL 权威回读。
        child_records = await repository.list_children_by_ids(
            [str(item.get("child_id") or "") for item in candidates if item.get("child_id")]
        )
        children_by_id = {str(record.child_id): record for record in child_records}
        active_version_set = {str(value) for value in active_version_ids}
        results = []
        for parent_id, group in sorted(grouped.items(), key=lambda pair: pair[1]["score"], reverse=True):
            parent = parents.get(parent_id)
            if parent is None:
                continue
            if isinstance(parent, dict):
                parent_text = parent.get("parent_text")
                parent_identity = parent.get("parent_id")
                parent_kb_id = parent.get("kb_id")
                parent_version = parent.get("version_id")
                parent_doc_id = parent.get("doc_id")
                parent_file_id = parent.get("file_id")
            else:
                parent_text = getattr(parent, "parent_text", None)
                parent_identity = getattr(parent, "parent_id", None)
                parent_kb_id = getattr(parent, "kb_id", None)
                parent_version = getattr(parent, "version_id", None)
                parent_doc_id = getattr(parent, "doc_id", None)
                parent_file_id = getattr(parent, "file_id", None)
            if parent_identity is not None and str(parent_identity) != parent_id:
                continue
            if parent_kb_id is not None and str(parent_kb_id) != str(kb_id):
                continue
            if parent_version is not None and str(parent_version) not in active_version_set:
                continue
            if not isinstance(parent_text, str):
                continue
            candidate_version = group["children"][0].get("version_id") if group.get("children") else None
            if candidate_version and parent_version and str(parent_version) != str(candidate_version):
                # Milvus 中残留的旧版本投影不能借用当前父块正文。
                continue
            child_hits = []
            for item in group["children"]:
                record = children_by_id.get(str(item["child_id"]))
                if record is None:
                    continue
                record_kb_id = getattr(record, "kb_id", None)
                record_parent_id = getattr(record, "parent_id", None)
                record_version_id = getattr(record, "version_id", None)
                if (
                    (record_kb_id is not None and str(record_kb_id) != str(kb_id))
                    or (record_parent_id is not None and str(record_parent_id) != parent_id)
                    or (record_version_id is not None and str(record_version_id) not in active_version_set)
                    or (record_version_id is not None and str(record_version_id) != str(item.get("version_id") or ""))
                ):
                    continue
                metadata = dict(item.get("meta_info") or {})
                metadata["spans"] = record.spans
                item["start_offset"] = record.start_offset
                item["end_offset"] = record.end_offset
                item["chunk_index"] = record.child_index
                child_hits.append(
                    {
                        "child_id": item["child_id"],
                        "parent_id": parent_id,
                        "score": item.get("score", 0.0),
                        "dense_score": item.get("dense_score"),
                        "sparse_score": item.get("sparse_score"),
                        "bm25_score": item.get("bm25_score"),
                        "rrf_score": item.get("rrf_score"),
                        "rerank_score": item.get("rerank_score"),
                        "chunk_index": item.get("chunk_index"),
                        "start_offset": item.get("start_offset"),
                        "end_offset": item.get("end_offset"),
                        "spans": metadata.get("spans", []),
                        "metadata": metadata,
                    }
                )
            if not child_hits:
                continue
            results.append(
                {
                    "id": parent_id,
                    "content": parent_text,
                    "score": group["score"],
                    "child_hits": child_hits,
                    "metadata": {
                        "result_type": "parent_child_parent",
                        "kb_id": kb_id,
                        "doc_id": parent_doc_id,
                        "file_id": parent_file_id,
                        "parent_id": parent_id,
                        "parent_score": group["score"],
                        "child_hits": child_hits,
                        "child_id": child_hits[0]["child_id"] if child_hits else None,
                        "child_index": child_hits[0].get("chunk_index") if child_hits else None,
                        "spans": child_hits[0].get("spans", []) if child_hits else [],
                    },
                }
            )
            if len(results) >= top_k_parent:
                break
        await cache_query(kb_id, cache_scope, fingerprint, results)
        return results
