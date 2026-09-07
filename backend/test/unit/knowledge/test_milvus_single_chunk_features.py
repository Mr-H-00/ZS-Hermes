from functools import partial
from types import SimpleNamespace

import pytest
from pymilvus import DataType

import yuxi.knowledge.implementations.milvus as milvus_module
import yuxi.models.rerank as rerank_module
from yuxi.knowledge.implementations.milvus import (
    CHILD_SPARSE_FIELD,
    CONTENT_SPARSE_FIELD,
    MilvusKB,
)
from yuxi.knowledge.read_models import KnowledgeBaseConfig


class FakeHit:
    """提供单层 Milvus 命中的最小替身。"""

    def __init__(self, chunk_id: str, score: float):
        self.distance = score
        self.entity = {
            "content": f"content-{chunk_id}",
            "chunk_id": chunk_id,
            "file_id": "file-1",
            "chunk_index": 0,
        }


class FakeCollection:
    """按向量字段返回固定候选并记录查询与写入。"""

    def __init__(self, results=None, *, include_model_sparse: bool = True):
        fields = []
        if include_model_sparse:
            fields.append(SimpleNamespace(name=CHILD_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR))
        self.schema = SimpleNamespace(fields=fields)
        self.results = results or {}
        self.search_calls = []
        self.insert_calls = []

    def search(self, **kwargs):
        """按 anns_field 返回测试候选。"""
        self.search_calls.append(kwargs)
        return [[FakeHit(chunk_id, score) for chunk_id, score in self.results.get(kwargs["anns_field"], [])]]

    def insert(self, entities):
        """记录 Milvus positional insert 数据。"""
        self.insert_calls.append(entities)


def make_kb(collection: FakeCollection) -> MilvusKB:
    """构造只装配单层检索依赖的 MilvusKB。"""
    kb = MilvusKB.__new__(MilvusKB)

    async def get_collection(kb_id, embedding_model_spec):
        """返回测试 collection。"""
        del kb_id, embedding_model_spec
        return collection

    async def build_file_expr(kb_id, file_name):
        """测试不启用文件名过滤。"""
        del kb_id, file_name
        return None

    async def hydrate_sources(kb_id, chunks):
        """模拟 PostgreSQL 文件名回读。"""
        del kb_id
        for chunk in chunks:
            chunk["metadata"]["source"] = "demo.md"

    kb._get_or_create_milvus_collection = get_collection
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]
    kb._build_file_name_expr = build_file_expr
    kb._hydrate_chunk_sources = hydrate_sources
    return kb


def make_config(*, sparse_enabled: bool) -> KnowledgeBaseConfig:
    """构造带单层 sparse 开关的知识库读取配置。"""
    return KnowledgeBaseConfig(
        kb_id="db",
        kb_type="milvus",
        embedding_model_spec="provider:BAAI/bge-m3",
        additional_params={
            "embedding_features": {"bge_m3_sparse_enabled": sparse_enabled},
            "parent_child": {"enabled": False},
        },
    )


def test_new_single_chunk_collection_always_defines_and_indexes_model_sparse_field(monkeypatch):
    """新建单层 collection 必须固定包含并索引模型 sparse 字段。"""
    created = {}

    class CreatedCollection:
        """记录 collection schema 与索引创建。"""

        def __init__(self, *, name, schema, using):
            created.update({"name": name, "schema": schema, "using": using, "indexes": []})

        def create_index(self, field_name, params):
            """记录索引字段与参数。"""
            created["indexes"].append((field_name, params))

    monkeypatch.setattr(milvus_module, "Collection", CreatedCollection)
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"

    kb._create_new_collection(
        "db",
        SimpleNamespace(dimension=2, model_id="BAAI/bge-m3"),
        "db",
    )

    assert CHILD_SPARSE_FIELD in {field.name for field in created["schema"].fields}
    assert CHILD_SPARSE_FIELD in {field_name for field_name, _params in created["indexes"]}


async def test_single_chunk_sparse_index_writes_provider_vectors_and_disabled_writes_empty_maps(monkeypatch):
    """单层写入必须保存真实 sparse 权重，关闭时只写空映射。"""

    class FakeChunkRepo:
        async def batch_upsert(self, chunks):
            """接受 PostgreSQL chunk 投影。"""
            return chunks

        async def delete_by_file_id(self, file_id):
            """提供失败补偿接口。"""
            del file_id

    class SparseModel:
        async def abatch_encode(self, texts, batch_size=40):
            """返回普通 dense 向量。"""
            del batch_size
            return [[0.1, 0.2] for _ in texts]

        async def abatch_encode_with_sparse(self, texts):
            """返回 provider 产生的 dense 与 lexical sparse 权重。"""
            return [[0.1, 0.2] for _ in texts], [{11: 0.7 + index} for index, _ in enumerate(texts)]

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    chunks = [
        {"id": "row-1", "content": "alpha", "chunk_id": "chunk-1", "file_id": "file-1", "chunk_index": 0},
        {"id": "row-2", "content": "beta", "chunk_id": "chunk-2", "file_id": "file-1", "chunk_index": 1},
    ]
    model = SparseModel()
    embedding_function = partial(model.abatch_encode, batch_size=40)
    sparse_collection = FakeCollection()

    await kb._embed_and_store_chunks(
        "db",
        "file-1",
        sparse_collection,
        chunks,
        embedding_function,
        sparse_enabled=True,
    )

    assert sparse_collection.insert_calls[0][6] == [{11: 0.7}, {11: 1.7}]

    dense_collection = FakeCollection()
    await kb._embed_and_store_chunks("db", "file-1", dense_collection, chunks, embedding_function)
    assert dense_collection.insert_calls[0][6] == [{}, {}]


async def test_single_chunk_sparse_index_rejects_provider_without_sparse_output():
    """启用 sparse 时 provider 缺少输出接口必须在写入前失败。"""
    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()
    chunks = [{"id": "row-1", "content": "alpha", "chunk_id": "chunk-1", "file_id": "file-1", "chunk_index": 0}]

    async def dense_only(texts):
        """模拟只有 dense 输出的 provider。"""
        return [[0.1, 0.2] for _ in texts]

    with pytest.raises(ValueError, match="provider 未提供 sparse"):
        await kb._embed_and_store_chunks(
            "db",
            "file-1",
            collection,
            chunks,
            dense_only,
            sparse_enabled=True,
        )

    assert collection.insert_calls == []


async def test_single_chunk_sparse_index_rejects_incomplete_provider_output():
    """provider 返回的 sparse 数量与 chunk 不一致时必须在 Milvus 写入前失败。"""

    class IncompleteSparseModel:
        """模拟 sparse 列数量缺失的外部 provider。"""

        async def abatch_encode(self, texts, batch_size=40):
            """返回完整 dense 列。"""
            del batch_size
            return [[0.1, 0.2] for _ in texts]

        async def abatch_encode_with_sparse(self, texts):
            """故意返回空 sparse 列。"""
            return [[0.1, 0.2] for _ in texts], []

    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()
    chunks = [{"id": "row-1", "content": "alpha", "chunk_id": "chunk-1", "file_id": "file-1", "chunk_index": 0}]
    model = IncompleteSparseModel()

    with pytest.raises(ValueError, match="different number"):
        await kb._embed_and_store_chunks(
            "db",
            "file-1",
            collection,
            chunks,
            partial(model.abatch_encode, batch_size=40),
            sparse_enabled=True,
        )

    assert collection.insert_calls == []


async def test_single_chunk_sparse_query_fuses_by_chunk_id_with_normalized_weights():
    """单层 dense 与 sparse 候选按 chunk_id 和归一化权重融合。"""
    collection = FakeCollection(
        {
            "embedding": [("a", 10.0), ("b", 0.0)],
            CHILD_SPARSE_FIELD: [("b", 10.0), ("c", 0.0)],
        }
    )
    kb = make_kb(collection)

    async def sparse_query_vector(embedding_model_spec, query_text):
        """返回确定性 sparse 查询向量。"""
        del embedding_model_spec, query_text
        return {11: 0.8}

    kb._get_sparse_query_vector = sparse_query_vector

    chunks = await kb.aquery(
        "query",
        "db",
        config=make_config(sparse_enabled=True),
        search_mode="vector",
        final_top_k=3,
        similarity_threshold=0.0,
        use_vector_score_fusion=True,
        dense_vector_weight=1.0,
        sparse_vector_weight=3.0,
    )

    assert [chunk["metadata"]["chunk_id"] for chunk in chunks] == ["b", "a", "c"]
    assert [chunk["score"] for chunk in chunks] == pytest.approx([0.75, 0.25, 0.0])
    assert {call["anns_field"] for call in collection.search_calls} == {"embedding", CHILD_SPARSE_FIELD}


async def test_single_chunk_sparse_query_rejects_legacy_collection_without_sparse_field():
    """旧 collection 缺少模型 sparse 字段时，开启融合必须显式失败。"""
    collection = FakeCollection({"embedding": [("a", 1.0)]}, include_model_sparse=False)
    kb = make_kb(collection)

    with pytest.raises(ValueError, match="bge_m3_sparse_vector"):
        await kb.aquery(
            "query",
            "db",
            config=make_config(sparse_enabled=True),
            use_vector_score_fusion=True,
        )

    assert collection.search_calls == []


async def test_single_chunk_rrf_unifies_vector_bm25_graph_before_rerank(monkeypatch):
    """单层 RRF 必须融合三路 chunk 排名后才把统一候选交给 reranker。"""
    collection = FakeCollection(
        {
            "embedding": [("a", 0.9), ("b", 0.8)],
            CONTENT_SPARSE_FIELD: [("b", 5.0)],
        }
    )
    kb = make_kb(collection)

    async def graph_candidates(*args, **kwargs):
        """返回只由图检索命中的 chunk。"""
        del args, kwargs
        return [
            {
                "content": "content-c",
                "metadata": {"chunk_id": "c", "file_id": "file-1", "chunk_index": 0},
                "score": 0.7,
                "graph_score": 0.7,
            }
        ]

    kb._retrieve_graph_chunks = graph_candidates
    rrf_completed = False
    original_rrf = kb._rrf_candidates

    def record_rrf(candidate_lists, *, identity_key):
        """记录 RRF 已在 rerank 前完成。"""
        nonlocal rrf_completed
        result = original_rrf(candidate_lists, identity_key=identity_key)
        rrf_completed = True
        return result

    kb._rrf_candidates = record_rrf
    rerank_documents = []

    class FakeReranker:
        async def acompute_score(self, payload, normalize=True):
            """断言 reranker 只接收已经统一的三路候选。"""
            assert normalize is True
            assert rrf_completed is True
            rerank_documents.extend(payload[1])
            return [0.1, 0.9, 0.2]

        async def aclose(self):
            """关闭测试 reranker。"""

    monkeypatch.setattr(rerank_module, "get_reranker", lambda model: FakeReranker())

    chunks = await kb.aquery(
        "query",
        "db",
        config=make_config(sparse_enabled=False),
        search_mode="hybrid",
        final_top_k=3,
        similarity_threshold=0.2,
        use_rrf=True,
        use_graph_retrieval=True,
        use_reranker=True,
        reranker_model="provider:reranker",
    )

    assert set(rerank_documents) == {"content-a", "content-b", "content-c"}
    assert {chunk["metadata"]["chunk_id"] for chunk in chunks} == {"a", "b", "c"}
    assert all("rrf_score" in chunk for chunk in chunks)
    assert collection.search_calls[0]["anns_field"] == "embedding"
    assert collection.search_calls[1]["anns_field"] == CONTENT_SPARSE_FIELD


async def test_single_chunk_graph_failure_is_observable_when_feature_enabled():
    """显式启用图检索后，图服务失败不得被转换为空结果。"""
    collection = FakeCollection({"embedding": [("a", 0.9)]})
    kb = make_kb(collection)

    async def fail_graph(*args, **kwargs):
        """模拟图检索服务不可用。"""
        del args, kwargs
        raise RuntimeError("graph unavailable")

    kb._retrieve_graph_chunks = fail_graph

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await kb.aquery(
            "query",
            "db",
            config=make_config(sparse_enabled=False),
            use_graph_retrieval=True,
        )
