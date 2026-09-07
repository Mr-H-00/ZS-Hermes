from __future__ import annotations

from uuid import uuid4

import pytest

from yuxi.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.storage.neo4j import get_shared_neo4j_connection, safe_neo4j_label


@pytest.mark.integration
def test_parent_child_graph_round_trip_preserves_explicit_ids():
    """真实 Neo4j 回读父子节点、关系和删除边界。"""
    kb_id = f"pytest_parent_graph_{uuid4().hex}"
    file_id = f"file_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    connection = get_shared_neo4j_connection()
    service = MilvusGraphService(neo4j_connection=connection)
    parent = {
        "parent_id": "parent-live-1",
        "file_id": file_id,
        "doc_id": "doc-live-1",
        "version_id": "version-live-1",
        "parent_index": 0,
        "parent_text": "张三任职于公司",
        "start_offset": 0,
        "end_offset": 8,
    }
    child = {
        "child_id": "child-live-1",
        "parent_id": "parent-live-1",
        "file_id": file_id,
        "doc_id": "doc-live-1",
        "version_id": "version-live-1",
        "child_index": 0,
        "child_text": "张三任职",
        "start_offset": 0,
        "end_offset": 4,
    }

    try:
        service.write_parent_child_graph(
            kb_id,
            parent,
            [child],
            normalize_extraction_result(
                {
                    "entities": [{"text": "张三", "label": "Person"}],
                    "relations": [],
                },
                "llm",
            ),
        )

        with connection.driver.session() as session:
            row = session.run(
                f"""
                MATCH (p:ParentChunk:MilvusKB:`{label}` {{parent_id: $parent_id}})
                      -[r:HAS_CHILD]->(c:ChildChunk:MilvusKB:`{label}` {{child_id: $child_id}})
                RETURN p.parent_id AS parent_id, c.child_id AS child_id, r.parent_id AS edge_parent_id
                """,
                parent_id=parent["parent_id"],
                child_id=child["child_id"],
            ).single()
        assert row is not None
        assert dict(row) == {
            "parent_id": "parent-live-1",
            "child_id": "child-live-1",
            "edge_parent_id": "parent-live-1",
        }
    finally:
        service._delete_file_graph_from_neo4j(kb_id, file_id)

    with connection.driver.session() as session:
        remaining = session.run(
            f"MATCH (n:MilvusKB:`{label}` {{file_id: $file_id}}) RETURN count(n) AS count",
            file_id=file_id,
        ).single()
    assert remaining["count"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parent_child_graph_version_filter_and_cleanup_preserve_active_replacement():
    """active 版本在 PPR 前过滤，且旧版本清理保留同文件的新投影。"""
    kb_id = f"pytest_parent_graph_version_{uuid4().hex}"
    file_id = f"file_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    connection = get_shared_neo4j_connection()
    service = MilvusGraphService(neo4j_connection=connection)

    try:
        seed_entity_id = None
        for suffix in ("old", "active"):
            entities, _triples = service.write_parent_child_graph(
                kb_id,
                {
                    "parent_id": f"parent-{suffix}",
                    "file_id": file_id,
                    "doc_id": f"doc-{suffix}",
                    "version_id": f"version-{suffix}",
                    "parent_index": 0,
                    "parent_text": f"{suffix} parent",
                    "start_offset": 0,
                    "end_offset": 6,
                },
                [
                    {
                        "child_id": f"child-{suffix}",
                        "parent_id": f"parent-{suffix}",
                        "file_id": file_id,
                        "doc_id": f"doc-{suffix}",
                        "version_id": f"version-{suffix}",
                        "child_index": 0,
                        "child_text": f"{suffix} child",
                        "start_offset": 0,
                        "end_offset": 5,
                    }
                ],
                normalize_extraction_result(
                    {"entities": [{"text": "共享实体", "label": "Version"}], "relations": []},
                    "llm",
                ),
            )
            seed_entity_id = entities[0]["entity_id"]

        subgraph = await service.query_seed_subgraph(
            kb_id,
            entity_ids=[seed_entity_id],
            max_nodes=20,
            version_ids=["version-active"],
        )
        returned_versions = {
            node["properties"].get("version_id")
            for node in subgraph["nodes"]
            if node["type"] in {"ParentChunk", "ChildChunk"}
        }
        assert returned_versions == {"version-active"}

        service._delete_parent_child_version_graph_from_neo4j(
            kb_id,
            file_id,
            "version-old",
        )

        with connection.driver.session() as session:
            versions = set(
                session.run(
                    f"""
                    MATCH (n:MilvusKB:`{label}` {{file_id: $file_id}})
                    WHERE n:ParentChunk OR n:ChildChunk
                    RETURN DISTINCT n.version_id AS version_id
                    """,
                    file_id=file_id,
                ).value("version_id")
            )
        assert versions == {"version-active"}
    finally:
        service._delete_file_graph_from_neo4j(kb_id, file_id)
