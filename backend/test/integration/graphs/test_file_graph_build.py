"""单文件图谱构建的真实 PostgreSQL、Neo4j 与 worker 装配契约。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.neo4j import safe_neo4j_label
from yuxi.migrations.schema import create_knowledge_tables
from yuxi.modules.knowledge.graphs import service as graph_service
from yuxi.modules.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import KnowledgeChunk
from yuxi.modules.knowledge.repositories import bases, chunks, graphs, projections
from yuxi.modules.knowledge.services import background_jobs

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_worker_builds_only_selected_file_with_shared_entities(monkeypatch):
    """目标文件完成，共享实体参与向量化，其他文件独有的待处理和失败记录不被触碰。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_file_graph_build")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    kb_id = f"pytest_file_build_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    for module in (bases, chunks, graphs, projections):
        monkeypatch.setattr(module, "pg_manager", manager)
    vector_store = SimpleNamespace(upsert_graph_records=AsyncMock())
    service = KnowledgeGraphService(kb_id=kb_id, graph_vector_store=vector_store)
    monkeypatch.setattr(background_jobs, "KnowledgeGraphService", lambda: service)
    monkeypatch.setattr(graph_service.GraphExtractorFactory, "create", lambda *_: SimpleNamespace(extractor_type="llm"))
    config = '{"graph_build_config":{"locked":true,"extractor_type":"llm","extractor_options":{}}}'
    a_result = normalize_extraction_result(
        {
            "entities": [{"text": "Shared"}, {"text": "Only A"}],
            "relations": [{"source": "Shared", "target": "Only A", "text": "A relation"}],
        },
        "llm",
    )
    b_result = normalize_extraction_result(
        {"entities": [{"text": "Shared"}, {"text": "Pending B"}, {"text": "Failed B"}], "relations": []}, "llm"
    )
    try:
        await create_knowledge_tables(manager)
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO knowledge_bases (kb_id,name,kb_type,additional_params) VALUES (:kb,:kb,'milvus',CAST(:config AS jsonb))"),
                {"kb": kb_id, "config": config},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_files (file_id,kb_id,filename,active_generation,is_folder) "
                    "VALUES ('a',:kb,'a',1,false),('b',:kb,'b',1,false)"
                ),
                {"kb": kb_id},
            )
            await conn.execute(
                insert(KnowledgeChunk),
                {
                    "chunk_id": "ca",
                    "file_id": "a",
                    "kb_id": kb_id,
                    "chunk_index": 0,
                    "generation": 1,
                    "content": "a",
                    "extraction_result": a_result,
                },
            )
            await conn.execute(
                insert(KnowledgeChunk),
                {
                    "chunk_id": "cb",
                    "file_id": "b",
                    "kb_id": kb_id,
                    "chunk_index": 0,
                    "generation": 1,
                    "content": "b",
                    "graph_structure_indexed": True,
                    "graph_extraction_details": {"status": "failed", "attempt_count": 3},
                },
            )

        b_chunk = SimpleNamespace(chunk_id="cb", file_id="b", chunk_index=0, content="b", start_char_pos=0, end_char_pos=1)
        entities, triples = await asyncio.to_thread(service.write_chunk_graph, kb_id, b_chunk, b_result)
        await service.graph_repo.upsert_chunk_graph(kb_id=kb_id, file_id="b", chunk_id="cb", entities=entities, triples=triples)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE knowledge_graph_entities SET vector_status='failed', vector_attempt_count=3 "
                    "WHERE normalized_name IN ('shared','failed b')"
                )
            )

        context = SimpleNamespace(
            payload={"kb_id": kb_id, "action": "build", "file_id": "a", "generation": 1},
            set_progress=AsyncMock(),
            set_result=AsyncMock(),
            raise_if_cancelled=AsyncMock(),
        )
        started, release = asyncio.Event(), asyncio.Event()

        async def hold_vector_write(**_):
            started.set()
            await release.wait()

        vector_store.upsert_graph_records.side_effect = hold_vector_write
        build = asyncio.create_task(background_jobs.run_knowledge_graph(context))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            with pytest.raises(DBAPIError, match="lock timeout"):
                async with engine.begin() as conn:
                    await conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
                    await conn.execute(text("UPDATE knowledge_files SET active_generation=2 WHERE file_id='a'"))
        finally:
            release.set()
            result = await asyncio.wait_for(build, timeout=15)
        assert result["success"] == 1 and result["remaining"] == 0
        assert result["extraction_failed"] == 0 and result["vector_failed"] == 0
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT chunk_id,graph_structure_indexed,graph_indexed,graph_extraction_details "
                        "FROM knowledge_chunks ORDER BY chunk_id"
                    )
                )
            ).all()
            assert [(row.chunk_id, row.graph_structure_indexed, row.graph_indexed) for row in rows] == [
                ("ca", True, True),
                ("cb", True, False),
            ]
            assert rows[1].graph_extraction_details["status"] == "failed"
            statuses = dict((await conn.execute(text("SELECT normalized_name,vector_status FROM knowledge_graph_entities"))).all())
            assert statuses == {"shared": "indexed", "only a": "indexed", "pending b": "pending", "failed b": "failed"}
            relations = (await conn.execute(text("SELECT content,vector_status FROM knowledge_graph_triples"))).all()
            assert (
                len(relations) == 1 and relations[0].vector_status == "indexed" and relations[0].content == "shared → RELATED_TO → only a"
            )

        # 目标文件和共享实体之前向量失败，重新构建只能重试目标范围。
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE knowledge_chunks SET graph_indexed=false WHERE chunk_id='ca'"))
            await conn.execute(
                text(
                    "UPDATE knowledge_graph_entities SET vector_status='failed', vector_attempt_count=3 "
                    "WHERE normalized_name IN ('shared','only a')"
                )
            )
            await conn.execute(text("UPDATE knowledge_graph_triples SET vector_status='failed', vector_attempt_count=3"))
        result = await asyncio.wait_for(background_jobs.run_knowledge_graph(context), timeout=15)
        assert result["success"] == 1 and result["vector_failed"] == 0
        async with engine.connect() as conn:
            statuses = dict((await conn.execute(text("SELECT normalized_name,vector_status FROM knowledge_graph_entities"))).all())
            assert statuses == {"shared": "indexed", "only a": "indexed", "pending b": "pending", "failed b": "failed"}
            assert await conn.scalar(text("SELECT graph_indexed FROM knowledge_chunks WHERE chunk_id='ca'")) is True
            assert await conn.scalar(text("SELECT vector_status FROM knowledge_graph_triples")) == "indexed"

        subgraph = await service.query_nodes(kb_id=kb_id, file_id="a", exclude_chunk=True)
        assert {node["name"].lower() for node in subgraph["nodes"]} == {"shared", "only a"}
        assert len(subgraph["edges"]) == 1
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE knowledge_files SET active_generation=2 WHERE file_id='a'"))
        with pytest.raises((ValueError, RuntimeError), match="代次|文件"):
            await background_jobs.run_knowledge_graph(context)
    finally:
        with service.driver.session() as session:
            session.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n").consume()
        await drop_isolated_schema(schema, admin, engine)
