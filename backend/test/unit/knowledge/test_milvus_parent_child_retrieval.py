from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.knowledge.implementations.milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.read_models import KnowledgeBaseConfig


class FakeHit:
    """模拟 Milvus child 查询命中。"""

    def __init__(self, entity: dict, distance: float):
        self.entity = entity
        self.distance = distance


class FakeChildCollection:
    """按检索字段返回确定性 child 候选。"""

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        field = kwargs["anns_field"]
        if field == "dense_vector":
            return [
                [
                    FakeHit(
                        {
                            "child_id": "child-a",
                            "parent_id": "parent-1",
                            "child_text": "dense child",
                            "file_id": "file-1",
                            "doc_id": "doc-1",
                            "version_id": "version-1",
                            "chunk_index": 0,
                            "meta_info": {"spans": [{"page_num": 1}]},
                        },
                        0.9,
                    ),
                    FakeHit(
                        {
                            "child_id": "child-b",
                            "parent_id": "parent-1",
                            "child_text": "second child",
                            "file_id": "file-1",
                            "doc_id": "doc-1",
                            "version_id": "version-1",
                            "chunk_index": 1,
                            "meta_info": {"spans": [{"page_num": 2}]},
                        },
                        0.8,
                    ),
                ]
            ]
        return [
            [
                FakeHit(
                    {
                        "child_id": "child-b",
                        "parent_id": "parent-1",
                        "child_text": "second child",
                        "file_id": "file-1",
                        "doc_id": "doc-1",
                        "version_id": "version-1",
                        "chunk_index": 1,
                        "meta_info": {"spans": [{"page_num": 2}]},
                    },
                    0.7,
                ),
                FakeHit(
                    {
                        "child_id": "child-c",
                        "parent_id": "parent-2",
                        "child_text": "keyword child",
                        "file_id": "file-2",
                        "doc_id": "doc-2",
                        "version_id": "version-2",
                        "chunk_index": 0,
                        "meta_info": {"spans": [{"page_num": 3}]},
                    },
                    0.6,
                ),
            ]
        ]


class FakeParentChildRepository:
    """提供 Parent-Child 查询所需的 PostgreSQL 回读数据。"""

    async def list_parents_by_ids(self, parent_ids: list[str]):
        return [
            SimpleNamespace(
                parent_id=parent_id,
                parent_text=f"full text {parent_id}",
                doc_id=f"doc-{parent_id[-1]}",
                file_id=f"file-{parent_id[-1]}",
            )
            for parent_id in parent_ids
        ]

    async def list_children_by_ids(self, child_ids: list[str]):
        return [
            SimpleNamespace(
                child_id=child_id,
                child_index=1,
                start_offset=10,
                end_offset=20,
                spans=[{"page_num": 9}],
            )
            for child_id in child_ids
        ]

    async def list_active_storage_targets(self, kb_id: str):
        """返回 active 版本及其持久化 collection 维度。"""
        return [("version-1", 2), ("version-2", 2)]


def _make_kb(collection: FakeChildCollection) -> MilvusKB:
    """构造不连接外部服务的 Parent-Child 查询执行器。"""
    kb = MilvusKB.__new__(MilvusKB)
    kb._get_existing_child_collection_for_query = lambda *args, **kwargs: _async_value(collection)
    kb._get_embedding_function = lambda *_args, **_kwargs: lambda texts: [[0.1, 0.2] for _ in texts]
    kb._build_file_name_expr = lambda *_args, **_kwargs: _async_value(None)
    return kb


async def _async_value(value):
    """返回异步测试替身的固定值。"""
    return value


def _config() -> KnowledgeBaseConfig:
    """构造启用 Parent-Child 的知识库配置。"""
    return KnowledgeBaseConfig(
        kb_id="kb-1",
        kb_type="milvus",
        embedding_model_spec="provider:model",
        additional_params={"parent_child": {"enabled": True}},
    )


@pytest.mark.asyncio
async def test_parent_child_query_rrf_aggregates_parent_and_reads_full_text(monkeypatch):
    """RRF 应在 child 粒度执行，rerank 前后同一 parent 只返回一次。"""
    collection = FakeChildCollection()
    kb = _make_kb(collection)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding", dimension=2),
    )
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepository)
    monkeypatch.setattr(milvus_module, "get_cached_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_query", lambda *_args: _async_value(None))

    results = await kb.aquery(
        "query",
        "kb-1",
        config=_config(),
        search_mode="hybrid",
        use_rrf=True,
        similarity_threshold=0.2,
        top_k_child=3,
        top_k_parent=2,
    )

    assert [result["id"] for result in results] == ["parent-1", "parent-2"]
    assert results[0]["content"] == "full text parent-1"
    assert len(results[0]["metadata"]["child_hits"]) == 2
    assert results[0]["metadata"]["child_hits"][0]["parent_id"] == "parent-1"
    assert results[0]["metadata"]["child_hits"][0]["spans"] == [{"page_num": 9}]
    assert results[0]["score"] == pytest.approx(1 / 61 + 1 / 62)
    assert {call["anns_field"] for call in collection.calls} == {"dense_vector", "content_sparse"}
    assert all('version_id in ["version-1", "version-2"]' in call["expr"] for call in collection.calls)


@pytest.mark.asyncio
async def test_parent_child_query_includes_graph_candidates_in_child_rrf(monkeypatch):
    """图谱命中必须以 child_id 参与 RRF，并最终聚合为父块。"""
    collection = FakeChildCollection()
    kb = _make_kb(collection)
    monkeypatch.setattr(
        milvus_module.model_cache, "get_model_info", lambda _spec: SimpleNamespace(model_type="embedding", dimension=2)
    )
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepository)
    monkeypatch.setattr(milvus_module, "get_cached_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_query", lambda *_args: _async_value(None))

    async def graph_candidates(*_args, **_kwargs):
        return [
            {
                "child_id": "child-c",
                "parent_id": "parent-2",
                "child_text": "graph child",
                "version_id": "version-2",
                "score": 0.8,
                "graph_score": 0.8,
                "meta_info": {"spans": [{"page_num": 4}]},
            }
        ]

    monkeypatch.setattr(kb, "_retrieve_parent_child_graph_candidates", graph_candidates)

    results = await kb.aquery(
        "query",
        "kb-1",
        config=_config(),
        search_mode="vector",
        use_graph_retrieval=True,
        use_rrf=True,
        top_k_parent=2,
    )

    graph_hit = next(hit for result in results for hit in result["child_hits"] if hit["child_id"] == "child-c")
    assert graph_hit["rrf_score"] == pytest.approx(1 / 61)
    assert graph_hit["metadata"]["spans"] == [{"page_num": 9}]


@pytest.mark.asyncio
async def test_parent_child_graph_candidates_filter_inactive_versions(monkeypatch):
    """图谱展开不得把非 active 版本的 child 候选带入 Parent-Child 查询。"""
    import yuxi.knowledge.graphs.milvus_graph_service as graph_service_module
    import yuxi.knowledge.graphs.milvus_graph_vector_store as vector_store_module

    class FakeVectorStore:
        async def search_entities(self, **_kwargs):
            return [{"id": "entity-1", "score": 1.0}]

        async def search_triples(self, **_kwargs):
            return []

    class FakeGraphService:
        calls = []

        async def query_and_rank_child_chunks_by_ppr(self, *_args, **_kwargs):
            self.calls.append(_kwargs)
            return [
                {"child_id": "active-child", "parent_id": "parent-active", "graph_score": 0.9},
                {"child_id": "stale-child", "parent_id": "parent-stale", "graph_score": 0.8},
            ]

    class FakeRepository:
        async def list_parents_by_ids(self, parent_ids):
            return [
                SimpleNamespace(
                    parent_id=parent_id,
                    kb_id="kb-1",
                    version_id="active-version",
                    ent_ids=["entity-1"],
                )
                for parent_id in parent_ids
            ]

        async def list_children_by_ids(self, child_ids):
            records = {
                "active-child": SimpleNamespace(
                    child_id="active-child",
                    parent_id="parent-active",
                    kb_id="kb-1",
                    version_id="active-version",
                    child_text="active",
                    file_id="file-1",
                    doc_id="doc-1",
                    child_index=0,
                    start_offset=0,
                    end_offset=6,
                    spans=[],
                    chunk_metadata={},
                ),
                "stale-child": SimpleNamespace(
                    child_id="stale-child",
                    parent_id="parent-stale",
                    kb_id="kb-1",
                    version_id="stale-version",
                    child_text="stale",
                    file_id="file-1",
                    doc_id="doc-1",
                    child_index=0,
                    start_offset=7,
                    end_offset=12,
                    spans=[],
                    chunk_metadata={},
                ),
            }
            return [records[child_id] for child_id in child_ids]

    monkeypatch.setattr(vector_store_module, "MilvusGraphVectorStore", FakeVectorStore)
    monkeypatch.setattr(graph_service_module, "MilvusGraphService", FakeGraphService)
    kb = MilvusKB.__new__(MilvusKB)
    candidates = await kb._retrieve_parent_child_graph_candidates(
        "query",
        "kb-1",
        [],
        {},
        "provider:model",
        ["active-version"],
        FakeRepository(),
    )

    assert [candidate["child_id"] for candidate in candidates] == ["active-child"]
    assert FakeGraphService.calls == [
        {
            "version_ids": ["active-version"],
            "max_nodes": 10000,
            "top_k": 20,
            "damping": 0.85,
        }
    ]


@pytest.mark.asyncio
async def test_parent_child_graph_retrieval_failure_is_observable(monkeypatch):
    """已启用的图检索故障不能被伪装为零命中。"""
    import yuxi.knowledge.graphs.milvus_graph_vector_store as vector_store_module

    class FailingVectorStore:
        def __init__(self):
            raise RuntimeError("graph vector unavailable")

    monkeypatch.setattr(vector_store_module, "MilvusGraphVectorStore", FailingVectorStore)
    kb = MilvusKB.__new__(MilvusKB)

    with pytest.raises(RuntimeError, match="Parent-Child graph retrieval failed"):
        await kb._retrieve_parent_child_graph_candidates(
            "query",
            "kb-1",
            [],
            {},
            "provider:model",
            ["active-version"],
            FakeParentChildRepository(),
        )


@pytest.mark.asyncio
async def test_parent_child_query_uses_cached_results_without_search(monkeypatch):
    """命中同一 active 版本查询缓存时不应再次访问 Milvus。"""
    collection = FakeChildCollection()
    kb = _make_kb(collection)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding", dimension=2),
    )
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepository)
    monkeypatch.setattr(milvus_module, "get_cached_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_parent", lambda *_args: _async_value(None))
    cached = [{"id": "parent-cached", "content": "cached", "score": 0.9}]
    cache_reads = []

    async def get_cached_query(*args):
        cache_reads.append(args)
        return cached

    monkeypatch.setattr(milvus_module, "get_cached_query", get_cached_query)

    result = await kb.aquery("query", "kb-1", config=_config(), top_k_child=2, top_k_parent=1)

    assert result == cached
    assert cache_reads
    assert collection.calls == []


@pytest.mark.asyncio
async def test_parent_child_query_fails_closed_when_active_versions_unavailable(monkeypatch):
    """PostgreSQL 无法确认 active 版本时不得访问 Milvus。"""

    class FailingParentChildRepository:
        """模拟 active 版本查询失败的 PostgreSQL repository。"""

        async def list_active_storage_targets(self, kb_id: str):
            """模拟 PostgreSQL active 存储目标读取失败。"""
            raise RuntimeError("postgres unavailable")

    kb = MilvusKB.__new__(MilvusKB)

    async def fail_if_collection_is_requested(*_args, **_kwargs):
        raise AssertionError("active 版本未知时不得访问 Milvus collection")

    kb._get_existing_child_collection_for_query = fail_if_collection_is_requested
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FailingParentChildRepository)

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await kb.aquery("query", "kb-1", config=_config())


@pytest.mark.asyncio
async def test_parent_child_query_uses_persisted_dimension_after_model_spec_changes(monkeypatch):
    """同一模型 spec 维度原地变化后仍只打开旧 active 版本的持久化 collection。"""

    class Repository(FakeParentChildRepository):
        """返回旧 active 版本写入时保存的维度。"""

        async def list_active_storage_targets(self, kb_id: str):
            """模拟模型配置变化前写入的 active 版本。"""
            return [("version-1", 1024)]

    class EmptyCollection:
        """记录 keyword 查询并返回空候选。"""

        def __init__(self) -> None:
            """初始化查询记录。"""
            self.calls = []

        def search(self, **kwargs):
            """保存查询参数。"""
            self.calls.append(kwargs)
            return [[]]

    kb = MilvusKB.__new__(MilvusKB)
    collection = EmptyCollection()
    requested_dimensions = []

    async def get_existing(dimension: int, **_kwargs):
        """只允许按 PostgreSQL 持久维度读取已有 collection。"""
        requested_dimensions.append(dimension)
        return collection

    async def reject_current_config(*_args, **_kwargs):
        """查询路径若按当前模型配置创建 collection 则让测试失败。"""
        raise AssertionError("Parent-Child query must not create a collection from current model metadata")

    kb._get_existing_child_collection_for_query = get_existing
    kb._get_or_create_collection_for_config = reject_current_config
    kb._build_file_name_expr = lambda *_args, **_kwargs: _async_value(None)
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", Repository)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding", dimension=3072),
    )
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_query", lambda *_args: _async_value(None))

    result = await kb.aquery("query", "kb-1", config=_config(), search_mode="keyword")

    assert result == []
    assert requested_dimensions == [1024]
    assert len(collection.calls) == 1


@pytest.mark.asyncio
async def test_parent_child_query_rejects_mixed_active_dimensions(monkeypatch):
    """多个 active 持久维度不能被旧缓存或单个 collection 静默覆盖。"""

    class Repository(FakeParentChildRepository):
        """返回无法由一次向量查询共同覆盖的多个持久维度。"""

        async def list_active_storage_targets(self, kb_id: str):
            """构造混合维度 active 版本。"""
            return [("version-1", 1024), ("version-2", 3072)]

    kb = MilvusKB.__new__(MilvusKB)

    async def reject_collection_access(*_args, **_kwargs):
        """混合维度必须在访问任何 collection 前失败。"""
        raise AssertionError("mixed dimensions must fail before collection access")

    kb._get_existing_child_collection_for_query = reject_collection_access
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", Repository)
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value([{"id": "stale"}]))

    with pytest.raises(ValueError, match="multiple embedding dimensions"):
        await kb.aquery("query", "kb-1", config=_config())


@pytest.mark.asyncio
async def test_parent_child_query_rejects_dense_vector_with_wrong_persisted_dimension(monkeypatch):
    """查询向量维度与 active 版本不符时必须在访问 Milvus 前失败。"""

    class Repository(FakeParentChildRepository):
        """返回唯一 active 版本的持久化维度。"""

        async def list_active_storage_targets(self, kb_id: str):
            """构造二维 active 存储目标。"""
            return [("version-1", 2)]

    kb = _make_kb(FakeChildCollection())
    kb._get_embedding_function = lambda *_args, **_kwargs: lambda _texts: [[0.1, 0.2, 0.3]]

    async def reject_collection_access(*_args, **_kwargs):
        """维度失配时不得读取或加载 Milvus collection。"""
        raise AssertionError("dimension mismatch must fail before collection access")

    kb._get_existing_child_collection_for_query = reject_collection_access
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", Repository)
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value(None))

    with pytest.raises(ValueError, match="query embedding dimension mismatch: expected 2"):
        await kb.aquery("query", "kb-1", config=_config(), search_mode="vector")


@pytest.mark.asyncio
async def test_parent_child_query_drops_missing_or_mismatched_child_records(monkeypatch):
    """PostgreSQL child 回读缺失或错配时不能返回错误父块。"""
    collection = FakeChildCollection()
    kb = _make_kb(collection)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding", dimension=2),
    )

    class Repository(FakeParentChildRepository):
        async def list_children_by_ids(self, child_ids):
            return [
                SimpleNamespace(
                    child_id=child_id,
                    parent_id="wrong-parent" if child_id == "child-a" else "parent-1",
                    kb_id="kb-1",
                    version_id="version-1",
                    child_index=1,
                    start_offset=101,
                    end_offset=120,
                    spans=[{"page_num": 99}],
                )
                for child_id in child_ids
                if child_id != "child-b"
            ]

    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", Repository)
    monkeypatch.setattr(milvus_module, "get_cached_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "get_cached_query", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "cache_query", lambda *_args: _async_value(None))

    results = await kb.aquery("query", "kb-1", config=_config(), search_mode="vector", top_k_parent=2)

    assert results == []


def test_parent_child_scoring_helpers_normalize_and_rrf_before_rerank():
    """Min-Max 单值边界、权重聚合和 RRF 常数必须保持确定性。"""
    dense = [{"child_id": "a", "score": 4.0}, {"child_id": "b", "score": 2.0}]
    sparse = [{"child_id": "b", "score": 5.0}, {"child_id": "c", "score": 1.0}]
    vector = MilvusKB._merge_vector_candidates(dense, sparse, 1.0, 2.0)
    assert [item["child_id"] for item in vector] == ["b", "a", "c"]
    assert vector[0]["score"] == pytest.approx(2 / 3)
    rrf = MilvusKB._rrf_candidates([dense, sparse])
    assert rrf[0]["child_id"] == "b"
    assert rrf[0]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62)


@pytest.mark.asyncio
async def test_sparse_vector_fusion_fails_closed_without_provider_capability(monkeypatch):
    """启用 sparse 融合但 provider 只有 dense 输出时不得伪装成功。"""

    class DenseOnlyModel:
        """无 sparse 接口的 embedding 替身。"""

    monkeypatch.setattr("yuxi.models.embed.select_embedding_model", lambda _spec: DenseOnlyModel())
    with pytest.raises(ValueError, match="sparse 输出"):
        await MilvusKB.__new__(MilvusKB)._get_sparse_query_vector("provider:model", "query")
