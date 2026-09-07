from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yuxi.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.knowledge.graphs.graph_utils import (
    ParentChildGraphMappingError,
    expand_parent_scores_to_child_candidates,
    validate_parent_child_graph_mapping,
)
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
import yuxi.knowledge.graphs.milvus_graph_service as graph_service_module


def _parent(**overrides):
    payload = {
        "parent_id": "parent-1",
        "file_id": "file-1",
        "doc_id": "doc-1",
        "version_id": "version-1",
        "parent_index": 0,
        "parent_text": "张三任职于公司",
        "start_offset": 0,
        "end_offset": 8,
    }
    payload.update(overrides)
    return payload


def _child(**overrides):
    payload = {
        "child_id": "child-1",
        "parent_id": "parent-1",
        "file_id": "file-1",
        "doc_id": "doc-1",
        "version_id": "version-1",
        "child_index": 0,
        "child_text": "张三任职",
        "start_offset": 0,
        "end_offset": 4,
    }
    payload.update(overrides)
    return payload


def test_parent_child_mapping_requires_explicit_matching_ids():
    """父子图谱不能用邻近记录补齐缺失或错配的 parent_id。"""
    with pytest.raises(ParentChildGraphMappingError, match="parent_id"):
        validate_parent_child_graph_mapping(_parent(), [_child(parent_id="other-parent")])

    with pytest.raises(ParentChildGraphMappingError, match="child_id"):
        validate_parent_child_graph_mapping(_parent(), [_child(child_id="")])


def test_parent_scores_expand_to_child_candidates_without_guessing():
    """父块图谱分数只展开到该父块显式声明的子块。"""
    result = expand_parent_scores_to_child_candidates(
        [("parent-1", 0.75)],
        {"parent-1": [_child(), _child(child_id="child-2", child_index=1)]},
    )

    assert result == [
        {"child_id": "child-1", "parent_id": "parent-1", "graph_score": 0.75},
        {"child_id": "child-2", "parent_id": "parent-1", "graph_score": 0.75},
    ]

    with pytest.raises(ParentChildGraphMappingError, match="没有已知子块"):
        expand_parent_scores_to_child_candidates([("missing-parent", 0.5)], {})


def test_parent_graph_write_uses_parent_and_child_nodes_and_edges():
    """Parent-Child 图谱写入不复用旧 Chunk 节点身份。"""
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    entities, triples = service.write_parent_child_graph(
        "kb_test",
        _parent(),
        [_child()],
        normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {"text": "张三", "label": "Person"},
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ]
            },
            "llm",
        ),
    )

    assert len(entities) == 2
    assert triples[0]["relation_type"] == "WORKS_AT"
    queries = [call.args[0] for call in tx.run.call_args_list]
    assert any("MERGE (p:ParentChunk:MilvusKB:`kb_test`" in query for query in queries)
    child_call = next(call for call in tx.run.call_args_list if "MERGE (c:ChildChunk" in call.args[0])
    assert child_call.kwargs["parent_id"] == "parent-1"
    assert "HAS_CHILD" in child_call.args[0]
    assert not any("MERGE (c:Chunk:MilvusKB" in query for query in queries)


def test_parent_graph_ppr_uses_parent_id_not_child_or_legacy_chunk_id():
    """ParentChunk PPR 结果的身份必须是 parent_id。"""
    subgraph = {
        "nodes": [
            {"id": "e1", "type": "Entity", "properties": {"entity_id": "seed"}},
            {"id": "p1", "type": "ParentChunk", "properties": {"parent_id": "parent-1"}},
        ],
        "edges": [{"source_id": "e1", "target_id": "p1"}],
    }

    assert (
        MilvusGraphService.rank_parent_chunks_by_ppr(
            subgraph,
            {"seed": 1.0},
            top_k=5,
            damping=0.85,
        )[0][0]
        == "parent-1"
    )


@pytest.mark.asyncio
async def test_parent_graph_subgraph_filters_versions_before_ppr_top_k(monkeypatch):
    """ParentChunk 子图必须在 PPR 与 top-k 前约束 active version。"""
    captured = {}

    async def capture_query(_func, *args, **_kwargs):
        captured["cypher"] = args[1]
        captured["version_ids"] = args[4]
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(graph_service_module, "_run_neo4j_query_io", capture_query)
    service = MilvusGraphService()

    await service.query_seed_subgraph(
        "kb_1",
        entity_ids=["entity-1"],
        max_nodes=20,
        version_ids=["active-version"],
    )

    assert "path_node.version_id IN $version_ids" in captured["cypher"]
    assert captured["version_ids"] == ["active-version"]


@pytest.mark.asyncio
async def test_parent_graph_version_cleanup_coordinates_all_stores(monkeypatch):
    """旧版本清理必须覆盖 Neo4j、PostgreSQL 引用和图向量。"""
    calls = []

    class FakeGraphRepository:
        async def list_parent_version_deletion_targets(self, version_id):
            calls.append(("targets", version_id))
            return ["entity-old"], ["triple-old"]

        async def delete_parent_version_references(self, version_id):
            calls.append(("postgres", version_id))
            return ["entity-old"], ["triple-old"]

    class FakeGraphVectorStore:
        async def delete_graph_records(self, kb_id, *, entity_ids, triple_ids):
            calls.append(("vectors", kb_id, entity_ids, triple_ids))

    service = MilvusGraphService(
        graph_repo=FakeGraphRepository(),
        graph_vector_store=FakeGraphVectorStore(),
    )
    monkeypatch.setattr(
        service,
        "_delete_parent_child_version_graph_from_neo4j",
        lambda kb_id, file_id, version_id: calls.append(("neo4j", kb_id, file_id, version_id)),
    )

    await service.delete_parent_child_version_graph("kb-1", "file-1", "version-old")

    assert calls == [
        ("targets", "version-old"),
        ("neo4j", "kb-1", "file-1", "version-old"),
        ("vectors", "kb-1", ["entity-old"], ["triple-old"]),
        ("postgres", "version-old"),
    ]
