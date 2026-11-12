"""按文件读取图谱的真实 PostgreSQL、Neo4j 与 HTTP 契约。"""

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema, scoped_manager
from yuxi.api.dependencies.knowledge import require_knowledge_base_read
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.knowledge import graphs as router
from yuxi.infrastructure.neo4j import safe_neo4j_label
from yuxi.migrations.schema import create_knowledge_tables
from yuxi.modules.knowledge.graphs import service as graph_service
from yuxi.modules.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import KnowledgeChunk
from yuxi.modules.knowledge.repositories import graphs, projections
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_file_subgraph_scopes_mentions_before_limit_and_rejects_other_files(monkeypatch):
    """共享实体不会带入其他文件关系，旧代次与其他库文件均不可见。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_file_graph")
    query_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    query_manager = scoped_manager(query_engine)
    query_manager.AsyncSession = async_sessionmaker(query_engine, expire_on_commit=False)
    kb_id = f"pytest_file_graph_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    monkeypatch.setattr(graphs, "pg_manager", query_manager)
    monkeypatch.setattr(projections, "pg_manager", query_manager)
    service = KnowledgeGraphService(kb_id=kb_id)
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(router.graph, prefix="/api")
    app.dependency_overrides[require_knowledge_base_read] = lambda: object()
    monkeypatch.setattr(router, "_get_graph_service", AsyncMock(return_value=service))

    try:
        await create_knowledge_tables(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO knowledge_bases (kb_id,name,kb_type) VALUES (:kb,:kb,'milvus'),('other','other','milvus')"),
                {"kb": kb_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_files (file_id,kb_id,filename,active_generation,is_folder) VALUES "
                    "('a',:kb,'a',2,false),('b',:kb,'b',1,false),('empty',:kb,'empty',1,false),"
                    "('solo',:kb,'solo',2,false),('mix',:kb,'mix',1,false),"
                    "('folder',:kb,'folder',1,true),('foreign','other','foreign',1,false)"
                ),
                {"kb": kb_id},
            )
            await conn.execute(
                insert(KnowledgeChunk),
                [
                    {"chunk_id": "ca", "file_id": "a", "kb_id": kb_id, "chunk_index": 0, "generation": 2, "content": "a"},
                    {"chunk_id": "old", "file_id": "a", "kb_id": kb_id, "chunk_index": 1, "generation": 1, "content": "old"},
                    {"chunk_id": "cb", "file_id": "b", "kb_id": kb_id, "chunk_index": 0, "generation": 1, "content": "b"},
                    {"chunk_id": "solo-current", "file_id": "solo", "kb_id": kb_id, "chunk_index": 0, "generation": 2, "content": "solo"},
                    {"chunk_id": "solo-old", "file_id": "solo", "kb_id": kb_id, "chunk_index": 1, "generation": 1, "content": "old"},
                    {
                        "chunk_id": "solo-pending",
                        "file_id": "solo",
                        "kb_id": kb_id,
                        "chunk_index": 2,
                        "generation": 2,
                        "content": "pending",
                    },
                    {"chunk_id": "mix-empty", "file_id": "mix", "kb_id": kb_id, "chunk_index": 0, "generation": 1, "content": "empty"},
                    {"chunk_id": "mix-entity", "file_id": "mix", "kb_id": kb_id, "chunk_index": 1, "generation": 1, "content": "entity"},
                ],
            )

        async def seed(chunk_id, file_id, names, relations, attribute):
            chunk = SimpleNamespace(chunk_id=chunk_id, file_id=file_id, chunk_index=0, content=chunk_id, start_char_pos=0, end_char_pos=1)
            payload = normalize_extraction_result(
                {
                    "entities": [{"text": name, "attributes": [{"text": attribute}]} for name in names],
                    "relations": relations,
                },
                "llm",
            )
            entities, triples = await asyncio.to_thread(service.write_chunk_graph, kb_id, chunk, payload)
            await service.graph_repo.upsert_chunk_graph(kb_id=kb_id, file_id=file_id, chunk_id=chunk_id, entities=entities, triples=triples)

        await seed("ca", "a", ["Alpha", "Beta", "Isolated", "Alpha Beta"], [], "visible")
        await seed("old", "a", ["Stale"], [], "old")
        await seed(
            "cb", "b", ["Alpha", "Beta", "Alpha   Beta"], [{"source": "Alpha", "target": "Beta", "text": "other relation"}], "other secret"
        )
        for chunk_id, file_id, names in (
            ("solo-current", "solo", []),
            ("solo-old", "solo", []),
            ("solo-pending", "solo", []),
            ("mix-empty", "mix", []),
            ("mix-entity", "mix", ["Mix entity"]),
        ):
            await seed(chunk_id, file_id, names, [], "visible")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE knowledge_chunks SET graph_structure_indexed = true WHERE chunk_id IN "
                    "('solo-current', 'solo-old', 'mix-empty', 'mix-entity')"
                )
            )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "solo"})
            assert response.status_code == 200, response.text
            assert {node["properties"].get("chunk_id") for node in response.json()["data"]["nodes"]} == {"solo-current"}
            assert response.json()["data"]["edges"] == []
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "solo", "exclude_chunk": "true"})
            assert response.json()["data"] == {"nodes": [], "edges": []}
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "mix"})
            data = response.json()["data"]
            assert {node["properties"]["chunk_id"] for node in data["nodes"] if node["type"] == "Chunk"} == {"mix-empty", "mix-entity"}
            assert {node["name"] for node in data["nodes"] if node["type"] == "Entity"} == {"Mix entity"}
            assert len(data["edges"]) == 1
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "mix", "exclude_chunk": "true"})
            assert [node["name"] for node in response.json()["data"]["nodes"]] == ["Mix entity"]
            response = await client.get(
                "/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a", "exclude_chunk": "true", "max_nodes": 4}
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert {node["name"].lower() for node in data["nodes"]} == {"alpha", "beta", "isolated", "alpha beta"}
            assert data["edges"] == []
            assert "other secret" not in response.text and "stale" not in response.text.lower()
            assert all(node["normalized"]["name"] == node["name"] for node in data["nodes"])
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"})
            assert len(response.json()["data"]["nodes"]) == 5
            assert len(response.json()["data"]["edges"]) == 4
            response = await client.get(
                "/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a", "exclude_chunk": "true", "node_label": "isolated"}
            )
            assert [node["name"] for node in response.json()["data"]["nodes"]] == ["Isolated"]
            response = await client.get(
                "/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a", "exclude_chunk": "true", "node_label": "alpha beta"}
            )
            assert [node["name"] for node in response.json()["data"]["nodes"]] == ["alpha beta"]
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "b", "exclude_chunk": "true"})
            assert response.status_code == 200, response.text
            assert [edge["properties"]["text"] for edge in response.json()["data"]["edges"]] == ["other relation"]
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "empty"})
            assert response.json()["data"] == {"nodes": [], "edges": []}
            for invalid in ("foreign", "folder", "missing"):
                response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": invalid})
                assert response.status_code == 404
            snapshot = service.graph_repo.file_graph_snapshot

            @asynccontextmanager
            async def failed_snapshot(*args):
                raise RuntimeError("PG unavailable")
                yield

            monkeypatch.setattr(service.graph_repo, "file_graph_snapshot", failed_snapshot)
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"})
            assert response.status_code == 500
            monkeypatch.setattr(service.graph_repo, "file_graph_snapshot", snapshot)
            neo_query = graph_service.run_neo4j_query_io
            monkeypatch.setattr(graph_service, "run_neo4j_query_io", AsyncMock(side_effect=RuntimeError("Neo4j unavailable")))
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"})
            assert response.status_code == 500

            # 两个请求共享单连接池；首个请求到达 Neo4j 前已取得全部 PG 锁。
            pool_started, pool_release = asyncio.Event(), asyncio.Event()

            async def held_pool_query(*args):
                pool_started.set()
                await pool_release.wait()
                return await neo_query(*args)

            monkeypatch.setattr(graph_service, "run_neo4j_query_io", held_pool_query)
            first = asyncio.create_task(client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"}))
            second = None
            try:
                await asyncio.wait_for(pool_started.wait(), timeout=3)
                second = asyncio.create_task(client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"}))
            finally:
                pool_release.set()
            responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
            assert all(response.status_code == 200 and len(response.json()["data"]["nodes"]) == 5 for response in responses)

            started, release = asyncio.Event(), asyncio.Event()

            async def held_query(*args):
                started.set()
                await release.wait()
                return await neo_query(*args)

            monkeypatch.setattr(graph_service, "run_neo4j_query_io", held_query)
            query_task = asyncio.create_task(client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a"}))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                with pytest.raises(DBAPIError, match="lock timeout"):
                    async with engine.begin() as conn:
                        await conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
                        await conn.execute(text("UPDATE knowledge_files SET active_generation = 1 WHERE file_id = 'a'"))
            finally:
                release.set()
                response = await query_task
            assert response.status_code == 200
            assert "stale" not in response.text.lower()
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE knowledge_files SET active_generation = 1 WHERE file_id = 'a'"))
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "file_id": "a", "exclude_chunk": "true"})
            assert [node["name"] for node in response.json()["data"]["nodes"]] == ["Stale"]
            # 共享实体最近由 b 写入；b 代次失效后，全库输出不能残留该来源的两份名称。
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE knowledge_files SET active_generation = 2 WHERE file_id IN ('a', 'b')"))
            response = await client.get("/api/graph/subgraph", params={"kb_id": kb_id, "exclude_chunk": "true"})
            assert response.status_code == 200, response.text
            shared = next(node for node in response.json()["data"]["nodes"] if node["name"] == "alpha beta")
            assert shared["normalized"]["name"] == "alpha beta"
            assert "source_chunk_id" not in shared["properties"]

    finally:
        with service.driver.session() as session:
            session.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n").consume()
        await query_engine.dispose()
        await drop_isolated_schema(schema, admin, engine)
