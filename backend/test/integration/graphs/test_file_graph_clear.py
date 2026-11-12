"""文件图谱撤销的真实 PostgreSQL、Neo4j 与投影并发契约。"""

import asyncio
import json
from datetime import timedelta
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
from yuxi.modules.background_jobs import repository as job_repository
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import KnowledgeChunk
from yuxi.modules.knowledge.repositories import bases, chunks, document_resets, graphs, projections
from yuxi.modules.knowledge.services import background_jobs
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError
from yuxi.shared.datetime import utc_now
from yuxi.workers.background_job_context import BackgroundJobContext

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def graph_data(monkeypatch):
    """为共享提及和结构半成品创建独立数据库与真实图投影。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_file_graph_clear")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    kb_id = f"pytest_clear_{uuid4().hex}"
    label = safe_neo4j_label(kb_id)
    for module in (bases, chunks, document_resets, graphs, projections):
        monkeypatch.setattr(module, "pg_manager", manager)
    vector_store = SimpleNamespace(delete_graph_records=AsyncMock())
    service = KnowledgeGraphService(kb_id=kb_id, graph_vector_store=vector_store)
    try:
        await create_knowledge_tables(manager)
        async with engine.begin() as conn:
            await conn.run_sync(lambda connection: BackgroundJobRecord.__table__.create(connection, checkfirst=True))
            await conn.execute(text("INSERT INTO knowledge_bases (kb_id,name,kb_type) VALUES (:kb,:kb,'milvus')"), {"kb": kb_id})
            await conn.execute(
                text(
                    "INSERT INTO knowledge_files (file_id,kb_id,filename,status,active_generation,is_folder,chunk_count) "
                    "VALUES ('a',:kb,'a','indexed',1,false,1),('b',:kb,'b','indexed',1,false,1)"
                ),
                {"kb": kb_id},
            )
        for file_id in ("a", "b"):
            payload = normalize_extraction_result(
                {
                    "entities": [
                        {"text": "Alpha", "attributes": [{"text": f"from-{file_id}"}]},
                        {"text": "Beta"},
                        {"text": f"Only {file_id}"},
                    ],
                    "relations": [{"source": "Alpha", "target": "Beta", "text": f"relation-{file_id}"}],
                },
                "llm",
            )
            async with engine.begin() as conn:
                await conn.execute(
                    insert(KnowledgeChunk),
                    {
                        "chunk_id": f"c{file_id}",
                        "kb_id": kb_id,
                        "file_id": file_id,
                        "generation": 1,
                        "chunk_index": 0,
                        "content": f"body-{file_id}",
                        "extraction_result": payload,
                        "graph_structure_indexed": True,
                        "graph_indexed": False,
                        "graph_extraction_details": {"status": "succeeded", "attempt_count": 1},
                    },
                )
            chunk = SimpleNamespace(
                chunk_id=f"c{file_id}", file_id=file_id, chunk_index=0, content=f"body-{file_id}", start_char_pos=0, end_char_pos=6
            )
            entities, triples = await asyncio.to_thread(service.write_chunk_graph, kb_id, chunk, payload)
            await service.graph_repo.upsert_chunk_graph(
                kb_id=kb_id, file_id=file_id, chunk_id=chunk.chunk_id, entities=entities, triples=triples
            )
        yield SimpleNamespace(service=service, engine=engine, kb_id=kb_id, label=label, vectors=vector_store)
    finally:
        with service.driver.session() as session:
            session.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n").consume()
        await drop_isolated_schema(schema, admin, engine)


async def test_clear_preserves_shared_graph_and_restores_surviving_attributes(graph_data, monkeypatch):
    """向量阶段未完成仍清理，失败回滚后重试，并从剩余提及恢复共享属性。"""
    data = graph_data
    service, kb_id = data.service, data.kb_id
    # 模拟历史缺陷留下的纯 Neo4j 半成品：对应 PG Chunk 已被删除。
    orphan = SimpleNamespace(chunk_id="gone", file_id="b", chunk_index=9, content="old", start_char_pos=0, end_char_pos=3)
    await asyncio.to_thread(
        service.write_chunk_graph, kb_id, orphan, normalize_extraction_result({"entities": [{"text": "Orphan"}], "relations": []}, "llm")
    )
    original_delete = service._delete_file_graph_from_neo4j
    with monkeypatch.context() as scoped:
        scoped.setattr(service, "_delete_file_graph_from_neo4j", lambda *args: (_ for _ in ()).throw(RuntimeError("neo4j unavailable")))
        with pytest.raises(RuntimeError, match="neo4j unavailable"):
            await service.clear_file_graph(kb_id, "b", generation=1)
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entity_mentions WHERE file_id='b'")) == 3
        assert await conn.scalar(text("SELECT extraction_result FROM knowledge_chunks WHERE chunk_id='cb'")) is not None
    assert service._delete_file_graph_from_neo4j == original_delete
    monkeypatch.setattr(background_jobs, "KnowledgeGraphService", lambda: service)
    context = SimpleNamespace(
        payload={"kb_id": kb_id, "action": "clear_file", "file_id": "b", "generation": 1},
        set_progress=AsyncMock(),
        set_result=AsyncMock(),
        raise_if_cancelled=AsyncMock(),
    )
    await background_jobs.run_knowledge_graph(context)
    await service.clear_file_graph(kb_id, "b", generation=1)
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT status FROM knowledge_files WHERE file_id='b'")) == "indexed"
        assert await conn.scalar(text("SELECT content FROM knowledge_chunks WHERE chunk_id='cb'")) == "body-b"
        cleared = (
            await conn.execute(
                text(
                    "SELECT graph_indexed, graph_structure_indexed, extraction_result, graph_extraction_details, ent_ids, tags "
                    "FROM knowledge_chunks WHERE chunk_id='cb'"
                )
            )
        ).one()
        assert tuple(cleared) == (False, False, None, {"status": "pending", "attempt_count": 0}, None, None)
        assert await conn.scalar(text("SELECT extraction_result FROM knowledge_chunks WHERE chunk_id='ca'")) is not None
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entity_mentions WHERE file_id='b'")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_triples")) == 1
        entity = (await conn.execute(text("SELECT name,attributes FROM knowledge_graph_entities WHERE normalized_name='alpha'"))).one()
        assert entity.name == "Alpha" and entity.attributes == [{"text": "from-a", "label": "Attribute"}]
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entities")) == 3
    with service.driver.session() as session:
        assert session.run(f"MATCH (c:Chunk:MilvusKB:`{data.label}` {{file_id:'b'}}) RETURN count(c) AS n").single()["n"] == 0
        node = session.run(f"MATCH (e:Entity:MilvusKB:`{data.label}` {{normalized_name:'alpha'}}) RETURN properties(e) AS e").single()["e"]
        assert node["source_chunk_id"] == "ca" and json.loads(node["attributes"]) == [{"text": "from-a", "label": "Attribute"}]
        assert session.run(f"MATCH (e:Entity:MilvusKB:`{data.label}` {{normalized_name:'orphan'}}) RETURN count(e) AS n").single()["n"] == 0
        assert session.run("MATCH ()-[r:RELATION {kb_id:$kb}]->() RETURN collect(r.file_id) AS files", kb=kb_id).single()["files"] == ["a"]
    # 保留的抽取正文足以再次构建，没有沿用被清除的缓存结果。
    payload = normalize_extraction_result({"entities": [{"text": "Rebuilt"}], "relations": []}, "llm")
    chunk = await service.chunk_repo.get_by_chunk_id("cb")
    entities, triples = await asyncio.to_thread(service.write_chunk_graph, kb_id, chunk, payload)
    await service.graph_repo.upsert_chunk_graph(kb_id=kb_id, file_id="b", chunk_id="cb", entities=entities, triples=triples)
    assert await service.graph_repo.count_by_kb_id(kb_id) == (4, 1)


async def test_clear_waits_for_projection_writes_and_fences_file_mutations(graph_data, monkeypatch):
    """排他清理等待旧写完成，清理中的代次切换被文件锁拒绝。"""
    data = graph_data
    async with projections.knowledge_projection_lock(data.kb_id, shared=True):
        clear = asyncio.create_task(data.service.clear_file_graph(data.kb_id, "b", generation=1))
        await asyncio.sleep(0.2)
        assert not clear.done()
    await asyncio.wait_for(clear, timeout=10)
    entered, release = asyncio.Event(), asyncio.Event()
    original = data.vectors.delete_graph_records

    async def hold_delete(*args, **kwargs):
        """在真实清理范围内暂停外部删除，以直接测试 PostgreSQL 文件锁。"""
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(data.vectors, "delete_graph_records", hold_delete)
    clear = asyncio.create_task(data.service.clear_file_graph(data.kb_id, "b", generation=1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(DBAPIError, match="lock timeout"):
            async with data.engine.begin() as conn:
                await conn.execute(text("SET LOCAL lock_timeout='200ms'"))
                await conn.execute(text("UPDATE knowledge_files SET active_generation=2 WHERE file_id='b'"))
    finally:
        release.set()
        await asyncio.wait_for(clear, timeout=10)
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET active_generation=2 WHERE file_id='b'"))
    with pytest.raises(StateConflictError, match="代次已改变"):
        await data.service.clear_file_graph(data.kb_id, "b", generation=1)
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET status='indexing' WHERE file_id='b'"))
    with pytest.raises(StateConflictError, match="当前状态"):
        await data.service.clear_file_graph(data.kb_id, "b", generation=2)
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET is_folder=true WHERE file_id='b'"))
    with pytest.raises(InvalidOperationError, match="目录"):
        await data.service.clear_file_graph(data.kb_id, "b", generation=2)
    with pytest.raises(ResourceNotFoundError):
        await data.service.clear_file_graph(data.kb_id, "missing", generation=1)


async def test_clear_rechecks_owner_after_waiting_for_projection_lock(graph_data, monkeypatch):
    """真实租约失效后，等待投影锁的旧清理不得恢复删除权限。"""
    data = graph_data
    manager = graphs.pg_manager
    monkeypatch.setattr(job_repository, "pg_manager", manager)
    monkeypatch.setattr(background_jobs, "KnowledgeGraphService", lambda: data.service)
    async with data.engine.begin() as conn:
        await conn.run_sync(lambda connection: BackgroundJobRecord.__table__.create(connection, checkfirst=True))
    job_id = uuid4().hex
    async with manager.get_async_session_context() as session:
        session.add(
            BackgroundJobRecord(
                id=job_id,
                name="lease boundary",
                type="knowledge_graph_index",
                status="running",
                payload={},
                worker_id="old-owner",
                started_at=utc_now(),
                heartbeat_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=30),
                attempt_count=1,
            )
        )
    original_extraction = (await data.service.chunk_repo.get_by_chunk_id("cb")).extraction_result
    context = BackgroundJobContext(
        job_id,
        "old-owner",
        {
            "kb_id": data.kb_id,
            "action": "clear_file",
            "file_id": "b",
            "generation": 1,
        },
    )
    async with projections.knowledge_projection_lock(data.kb_id, shared=True):
        clear = asyncio.create_task(background_jobs.run_knowledge_graph(context))
        async with asyncio.timeout(5):
            while True:
                async with data.engine.connect() as conn:
                    progress = await conn.scalar(text("SELECT progress FROM background_jobs WHERE id=:id"), {"id": job_id})
                if progress == 5:
                    break
                await asyncio.sleep(0.01)
        assert not clear.done()
        async with data.engine.begin() as conn:
            await conn.execute(
                text("UPDATE background_jobs SET status='failed', lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                {"id": job_id},
            )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(clear, timeout=5)
    assert context.cancellation_reason == "lease_lost"
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entity_mentions WHERE file_id='b'")) == 3
        assert await conn.scalar(text("SELECT extraction_result FROM knowledge_chunks WHERE chunk_id='cb'")) == original_extraction
    with data.service.driver.session() as session:
        assert session.run(f"MATCH (c:Chunk:MilvusKB:`{data.label}` {{file_id:'b'}}) RETURN count(c) AS n").single()["n"] == 1


@pytest.mark.parametrize("failure_stage", ["pg_commit", "vectors", "after_neo4j"])
async def test_clear_recovers_historical_orphans_across_failure_windows(graph_data, monkeypatch, failure_stage):
    """历史 Chunk 已丢失时仍按文件来源清孤儿，任一提交窗口失败后可以完整重放。"""
    from sqlalchemy import event

    data = graph_data
    service = data.service
    payload = normalize_extraction_result(
        {
            "entities": [{"text": "Lost One"}, {"text": "Lost Two"}],
            "relations": [{"source": "Lost One", "target": "Lost Two", "text": "lost"}],
        },
        "llm",
    )
    await service.chunk_repo.batch_upsert(
        [
            {
                "chunk_id": "lost",
                "kb_id": data.kb_id,
                "file_id": "b",
                "generation": 1,
                "chunk_index": 99,
                "content": "lost",
                "extraction_result": payload,
            }
        ]
    )
    chunk = await service.chunk_repo.get_by_chunk_id("lost")
    entities, triples = await asyncio.to_thread(service.write_chunk_graph, data.kb_id, chunk, payload)
    await service.graph_repo.upsert_chunk_graph(
        kb_id=data.kb_id,
        file_id="b",
        chunk_id="lost",
        entities=entities,
        triples=triples,
    )
    async with data.engine.begin() as conn:
        await conn.execute(text("DELETE FROM knowledge_chunks WHERE chunk_id='lost'"))

    def mark_orphan_delete(conn, _cursor, statement, _parameters, _context, _many):
        """只在历史孤儿预清事务的真实 DELETE 后注入提交故障。"""
        if statement.startswith("DELETE FROM knowledge_graph_triples"):
            conn.info["fail_orphan_commit"] = True

    def fail_commit(conn):
        """在 DBAPI commit 前失败，让 session 回滚真实 SQL。"""
        if conn.info.pop("fail_orphan_commit", False):
            raise RuntimeError("orphan commit failed")

    original_delete = service._delete_file_graph_from_neo4j

    def fail_after_neo4j(*args):
        """保留外部删除已提交但普通提及事务尚未提交的失败窗口。"""
        original_delete(*args)
        raise RuntimeError("after neo4j failed")

    with monkeypatch.context() as scoped:
        if failure_stage == "pg_commit":
            event.listen(data.engine.sync_engine, "before_cursor_execute", mark_orphan_delete)
            event.listen(data.engine.sync_engine, "commit", fail_commit)
        elif failure_stage == "vectors":
            scoped.setattr(data.vectors, "delete_graph_records", AsyncMock(side_effect=RuntimeError("vectors failed")))
        else:
            scoped.setattr(service, "_delete_file_graph_from_neo4j", fail_after_neo4j)
        try:
            with pytest.raises(RuntimeError, match="failed"):
                await service.clear_file_graph(data.kb_id, "b", generation=1)
        finally:
            if failure_stage == "pg_commit":
                event.remove(data.engine.sync_engine, "before_cursor_execute", mark_orphan_delete)
                event.remove(data.engine.sync_engine, "commit", fail_commit)
    async with data.engine.connect() as conn:
        lost_count = await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entities WHERE normalized_name LIKE 'lost %'"))
        assert lost_count == (2 if failure_stage == "pg_commit" else 0)
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_entity_mentions WHERE file_id='b'")) == 3
    with service.driver.session() as session:
        lost_count = session.run(f"MATCH (c:Chunk:MilvusKB:`{data.label}` {{chunk_id:'lost'}}) RETURN count(c) AS n").single()["n"]
        assert lost_count == (0 if failure_stage == "after_neo4j" else 1)
    await service.clear_file_graph(data.kb_id, "b", generation=1)
    async with data.engine.connect() as conn:
        names = list(await conn.scalars(text("SELECT normalized_name FROM knowledge_graph_entities ORDER BY normalized_name")))
        assert names == ["alpha", "beta", "only a"]
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_triples")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_graph_triple_mentions WHERE file_id='b'")) == 0
    with service.driver.session() as session:
        assert session.run(f"MATCH (c:Chunk:MilvusKB:`{data.label}` {{file_id:'b'}}) RETURN count(c) AS n").single()["n"] == 0


@pytest.mark.parametrize("trusted_source", [True, False])
async def test_clear_restores_only_trustworthy_surviving_entity_source(graph_data, trusted_source):
    """较早提及没有正文时继续找可信来源，全部缺失时不发布已撤销文件的属性。"""
    data = graph_data
    service = data.service
    payload = normalize_extraction_result({"entities": [{"text": "Alpha", "attributes": [{"text": "untrusted"}]}], "relations": []}, "llm")
    await service.chunk_repo.batch_upsert(
        [
            {
                "chunk_id": "aaa",
                "kb_id": data.kb_id,
                "file_id": "a",
                "generation": 1,
                "chunk_index": 2,
                "content": "incomplete extraction",
            }
        ]
    )
    chunk = await service.chunk_repo.get_by_chunk_id("aaa")
    entities, triples = await asyncio.to_thread(service.write_chunk_graph, data.kb_id, chunk, payload)
    await service.graph_repo.upsert_chunk_graph(kb_id=data.kb_id, file_id="a", chunk_id="aaa", entities=entities, triples=triples)
    if not trusted_source:
        async with data.engine.begin() as conn:
            await conn.execute(text("UPDATE knowledge_chunks SET extraction_result=NULL WHERE chunk_id='ca'"))
    await service.clear_file_graph(data.kb_id, "b", generation=1)
    expected_attributes = [{"text": "from-a", "label": "Attribute"}] if trusted_source else []
    async with data.engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT attributes FROM knowledge_graph_entities WHERE normalized_name='alpha'")) == expected_attributes
        )
    with service.driver.session() as session:
        node = session.run(f"MATCH (e:Entity:MilvusKB:`{data.label}` {{normalized_name:'alpha'}}) RETURN properties(e) AS e").single()["e"]
        assert json.loads(node["attributes"]) == expected_attributes
        assert node.get("source_chunk_id") == ("ca" if trusted_source else None)
