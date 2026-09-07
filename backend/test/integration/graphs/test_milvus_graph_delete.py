from __future__ import annotations

from uuid import uuid4

import pytest

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.storage.neo4j import get_shared_neo4j_connection, safe_neo4j_label


@pytest.mark.integration
def test_delete_file_graph_preserves_shared_and_unrelated_entities():
    kb_id = f"pytest_delete_graph_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    connection = get_shared_neo4j_connection()
    service = MilvusGraphService(neo4j_connection=connection)

    try:
        with connection.driver.session() as session:
            session.run(
                f"""
                CREATE (f1:Chunk:MilvusKB:`{label}` {{kb_id: $kb_id, file_id: 'f1'}}),
                       (f2:Chunk:MilvusKB:`{label}` {{kb_id: $kb_id, file_id: 'f2'}}),
                       (shared:Entity:MilvusKB:`{label}` {{kb_id: $kb_id, name: 'shared'}}),
                       (f1_only:Entity:MilvusKB:`{label}` {{kb_id: $kb_id, name: 'f1_only'}}),
                       (unrelated_orphan:Entity:MilvusKB:`{label}` {{kb_id: $kb_id, name: 'unrelated_orphan'}}),
                       (f1)-[:MENTIONS {{kb_id: $kb_id, file_id: 'f1'}}]->(shared),
                       (f1)-[:MENTIONS {{kb_id: $kb_id, file_id: 'f1'}}]->(f1_only),
                       (f2)-[:MENTIONS {{kb_id: $kb_id, file_id: 'f2'}}]->(shared)
                """,
                kb_id=kb_id,
            ).consume()

        service._delete_file_graph_from_neo4j(kb_id, "f1")

        with connection.driver.session() as session:
            remaining_entities = set(
                session.run(f"MATCH (e:Entity:MilvusKB:`{label}`) RETURN e.name AS name").value("name")
            )
            remaining_files = set(
                session.run(f"MATCH (c:Chunk:MilvusKB:`{label}`) RETURN c.file_id AS file_id").value("file_id")
            )

        assert remaining_entities == {"shared", "unrelated_orphan"}
        assert remaining_files == {"f2"}

        service._delete_file_graph_from_neo4j(kb_id, "f2")

        with connection.driver.session() as session:
            remaining_entities = set(
                session.run(f"MATCH (e:Entity:MilvusKB:`{label}`) RETURN e.name AS name").value("name")
            )

        assert remaining_entities == {"unrelated_orphan"}
    finally:
        with connection.driver.session() as session:
            session.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n").consume()


@pytest.mark.integration
def test_delete_graph_removes_only_target_knowledge_base():
    """知识库资源清理应删除目标 Neo4j 图，同时保留相邻知识库。"""
    target_kb_id = f"pytest_delete_kb_{uuid4().hex}"
    neighbor_kb_id = f"pytest_keep_kb_{uuid4().hex}"
    target_label = safe_neo4j_label(target_kb_id)
    neighbor_label = safe_neo4j_label(neighbor_kb_id)
    connection = get_shared_neo4j_connection()
    service = MilvusGraphService(neo4j_connection=connection)

    try:
        with connection.driver.session() as session:
            session.run(
                f"CREATE (:ParentChunk:MilvusKB:`{target_label}` {{kb_id: $kb_id, parent_id: 'target'}})",
                kb_id=target_kb_id,
            ).consume()
            session.run(
                f"CREATE (:ParentChunk:MilvusKB:`{neighbor_label}` {{kb_id: $kb_id, parent_id: 'neighbor'}})",
                kb_id=neighbor_kb_id,
            ).consume()

        service.delete_graph(target_kb_id)

        with connection.driver.session() as session:
            target_count = session.run(f"MATCH (n:MilvusKB:`{target_label}`) RETURN count(n) AS count").single()[
                "count"
            ]
            neighbor_count = session.run(f"MATCH (n:MilvusKB:`{neighbor_label}`) RETURN count(n) AS count").single()[
                "count"
            ]
        assert target_count == 0
        assert neighbor_count == 1
    finally:
        with connection.driver.session() as session:
            session.run(f"MATCH (n:MilvusKB:`{target_label}`) DETACH DELETE n").consume()
            session.run(f"MATCH (n:MilvusKB:`{neighbor_label}`) DETACH DELETE n").consume()
