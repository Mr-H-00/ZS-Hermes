"""真实 PostgreSQL 的代次、删除与局部 outbox 并发边界。"""

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager as _create_isolated_manager
from test.integration.support.postgres_schema import drop_isolated_schema as _drop_isolated_schema
from yuxi.api.routers.knowledge import management as knowledge_router
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.migrations.schema import create_knowledge_tables
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.models import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeFile,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
    KnowledgeProjectionOutbox,
)
from yuxi.modules.knowledge.repositories import files
from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.graphs import KnowledgeGraphRepository
from yuxi.modules.knowledge.services import projection_cleanup
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture()
async def database(monkeypatch):
    """给真实 repositories 提供独立 Schema 与 owning transaction。"""
    schema, admin, engine, manager = await _create_isolated_manager("pytest_projection")
    await create_knowledge_tables(manager)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_connection: BackgroundJobRecord.__table__.create(sync_connection, checkfirst=True))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        """被测代码拥有提交点。"""
        async with sessions() as session, session.begin():
            yield session

    monkeypatch.setattr(files.pg_manager, "get_async_session_context", session_context)
    async with sessions() as session, session.begin():
        session.add(KnowledgeBase(kb_id="kb-test", name="test", kb_type="milvus"))
    try:
        yield sessions
    finally:
        await close_async_redis_client()
        await _drop_isolated_schema(schema, admin, engine)


async def _file():
    """创建可索引的文件事实。"""
    return await KnowledgeFileRepository().upsert(
        "file-test", {"kb_id": "kb-test", "filename": "test.md", "status": "indexing", "markdown_file": "document.md"}
    )


async def _running_job(database, job_id, owner):
    """建立真实执行资格，测试只改变自己的作业记录。"""
    async with database() as session, session.begin():
        session.add(
            BackgroundJobRecord(
                id=job_id,
                name="OCR fixture",
                type="knowledge_parse",
                status="running",
                worker_id=owner,
                lease_expires_at=utc_now() + timedelta(minutes=1),
            )
        )


async def test_competing_parse_cannot_overwrite_winner_parameters(database, monkeypatch, tmp_path):
    """真实 worker 用例竞争文件，落败请求不能改变胜出产物的参数事实。"""
    from yuxi.modules.knowledge.services import background_jobs as jobs

    repo = KnowledgeFileRepository()
    await repo.upsert("file-test", {"kb_id": "kb-test", "filename": "scan.pdf", "path": "minio://fixture/scan.pdf", "status": "uploaded"})
    await _running_job(database, "parse-a", "worker-a")
    await _running_job(database, "parse-b", "worker-b")
    executor = object.__new__(MilvusKB)
    entered, release = asyncio.Event(), asyncio.Event()

    async def parse_source(*, params, **kwargs):
        """固定胜出任务已读参数的时点，外部 OCR 不参与事务竞争。"""
        selected = params["ocr_engine"]
        entered.set()
        await release.wait()
        return selected

    async def save(kb_id, file_id, attempt_id, content):
        """以实际文件字节观察已发布的解析结果。"""
        (tmp_path / "document.md").write_text(content)
        return "minio://fixture/result/document.md"

    monkeypatch.setattr(
        jobs.knowledge_base, "get_kb_config", AsyncMock(return_value=SimpleNamespace(kb_type="milvus", additional_params={}))
    )
    monkeypatch.setattr(jobs.knowledge_base, "_get_or_create_kb_instance", AsyncMock(return_value=executor))
    monkeypatch.setattr("yuxi.modules.documents.service.parse_to_hosted_markdown", parse_source)
    monkeypatch.setattr("yuxi.modules.knowledge.base.get_minio_client", lambda: SimpleNamespace(KB_BUCKETS={"images": "images"}))
    monkeypatch.setattr(executor, "save_markdown_artifact", save)

    async def process(job_id, worker_id, engine):
        """从正式单文件 worker 用例执行到真实数据库发布。"""
        return await jobs._process_document_file(
            SimpleNamespace(job_id=job_id, worker_id=worker_id),
            kb_id="kb-test",
            file_id="file-test",
            action="parse",
            params={"ocr_engine": engine},
            operator_id="operator",
        )

    winner = asyncio.create_task(process("parse-a", "worker-a", "rapid_ocr"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        loser = await process("parse-b", "worker-b", "mineru_ocr")
        assert loser["status"] == "failed"
        release.set()
        assert (await asyncio.wait_for(winner, 5))["status"] == "parsed"
        record = await repo.get_by_file_id("file-test")
        assert record.processing_params["ocr_engine"] == (tmp_path / "document.md").read_text() == "rapid_ocr"
        assert record.processing_owner is None
    finally:
        release.set()
        await winner


@pytest.mark.parametrize("owner", [{"processing_job_id": "job"}, {"processing_owner": "worker"}])
@pytest.mark.parametrize("operation", ["claim", "index"])
async def test_partial_execution_identity_cannot_modify_file(database, owner, operation):
    """缺少作业或执行者的调用不能借无 Owner 路径修改文件。"""
    await _file()
    repo = KnowledgeFileRepository()
    with pytest.raises(ValueError, match="job and worker"):
        if operation == "claim":
            await repo.update_fields_if_status(
                kb_id="kb-test", file_id="file-test", allowed_statuses={"indexing"}, data={"status": "parsed"}, **owner
            )
        else:
            await repo.begin_index_generation(kb_id="kb-test", file_id="file-test", **owner)
    record = await repo.get_by_file_id("file-test")
    assert record.status == "indexing"
    assert record.building_generation is None


@pytest.mark.parametrize("wait_for_file", [False, True])
async def test_expired_index_job_cannot_activate_generation(database, wait_for_file):
    """租约过期但尚未恢复清理时，旧 owner 也不能激活代次。"""
    await _file()
    repo = KnowledgeFileRepository()
    await _running_job(database, "index-job", "worker")
    await repo.update_fields(file_id="file-test", data={"processing_job_id": "index-job", "processing_owner": "worker"})
    owner = {"processing_job_id": "index-job", "processing_owner": "worker"}
    generation = await repo.begin_index_generation(kb_id="kb-test", file_id="file-test", **owner)
    async with database() as session, session.begin():
        job = await session.get(BackgroundJobRecord, "index-job")
        job.lease_expires_at = utc_now() + timedelta(seconds=5 if wait_for_file else -1)

    async def finish():
        """调用真实发布事务，保留其异常。"""
        return await repo.finish_index_generation(
            kb_id="kb-test", file_id="file-test", generation=generation, chunk_count=2, token_count=3, **owner
        )

    if wait_for_file:
        async with database() as blocker, blocker.begin():
            await blocker.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == "file-test").with_for_update())
            blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            pending = asyncio.create_task(finish())
            # 先观察真实 PostgreSQL 等锁事实，再让租约到期，排除慢调度造成的假通过。
            async with asyncio.timeout(8), database() as observer:
                while not await observer.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)))"),
                    {"pid": blocker_pid},
                ):
                    await asyncio.sleep(0.01)
                job = await observer.get(BackgroundJobRecord, "index-job")
                database_now = await observer.scalar(select(func.clock_timestamp()))
                assert job.lease_expires_at > database_now
                await asyncio.sleep((job.lease_expires_at - database_now).total_seconds() + 0.05)
        with pytest.raises(ValueError, match="owner"):
            await pending
    else:
        with pytest.raises(ValueError, match="owner"):
            await finish()
    record = await repo.get_by_file_id("file-test")
    assert record.active_generation == 1 and record.building_generation == generation
    assert record.status == "indexing" and record.chunk_count == 0
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeProjectionOutbox)) == 0


async def test_new_generation_isolates_old_tail_and_cleanup_bounds_generation_retention(database, monkeypatch):
    """构建不复用代次；旧内容不可见，清理后只保留 active 版本。"""
    await _file()
    repo = KnowledgeFileRepository()
    first = await repo.begin_index_generation(kb_id="kb-test", file_id="file-test")
    current = await repo.begin_index_generation(kb_id="kb-test", file_id="file-test")
    assert current > first
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": "current",
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_index": 0,
                "generation": current,
                "content": "current",
            },
            {
                "chunk_id": "late-old",
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_index": 1,
                "generation": first,
                "content": "must not leak",
            },
        ]
    )
    await repo.finish_index_generation(kb_id="kb-test", file_id="file-test", generation=current, chunk_count=1, token_count=2)
    assert [item.content for item in await KnowledgeChunkRepository().list_by_file_id("file-test")] == ["current"]
    with pytest.raises(ValueError, match="owner"):
        await repo.finish_index_generation(kb_id="kb-test", file_id="file-test", generation=first, chunk_count=2, token_count=3)

    async def delete_chunks(kb_id, file_id, generation):
        """此例核对 PG 最终事实；真实 Milvus 清理由 E2E 覆盖。"""
        await KnowledgeChunkRepository().delete_by_file_id(file_id, generation=generation)

    monkeypatch.setattr(
        projection_cleanup.knowledge_base,
        "get_cleanup_executor",
        AsyncMock(return_value=SimpleNamespace(delete_file_chunks_only=delete_chunks)),
    )
    assert len(await projection_cleanup.process_knowledge_projections()) == 2
    async with database() as session:
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file-test"))
        assert file.active_generation == current
        assert file.building_generation is None
        assert await session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 1
        assert set(await session.scalars(select(KnowledgeProjectionOutbox.status))) == {"applied"}


async def test_deleted_knowledge_base_rejects_activation_and_new_files(database):
    """删除后即使旧写入完成，也不能激活或在 tombstone 下新建文件。"""
    await _file()
    repo = KnowledgeFileRepository()
    generation = await repo.begin_index_generation(kb_id="kb-test", file_id="file-test")
    await KnowledgeBaseRepository().mark_deleted("kb-test")
    with pytest.raises(ValueError, match="deleted"):
        await repo.finish_index_generation(kb_id="kb-test", file_id="file-test", generation=generation, chunk_count=1, token_count=2)
    with pytest.raises(ValueError, match="deleted"):
        await repo.upsert("new-file", {"kb_id": "kb-test", "filename": "new.md"})
    assert await repo.get_by_file_id("file-test") is None
    assert await KnowledgeBaseRepository().get_by_kb_id("kb-test") is None


async def test_file_owner_rejects_replaced_callback_even_with_live_job(database):
    """有效 Job 也不能越过已被替换的文件执行身份。"""
    await _file()
    repo = KnowledgeFileRepository()
    await _running_job(database, "progress-old", "old")
    await repo.update_fields(file_id="file-test", data={"processing_job_id": "progress-old", "processing_owner": "old"})
    generation = await repo.begin_index_generation(
        kb_id="kb-test", file_id="file-test", processing_job_id="progress-old", processing_owner="old"
    )
    await repo.update_fields(file_id="file-test", data={"processing_job_id": "progress-new", "processing_owner": "new"})
    with pytest.raises(ValueError, match="owner"):
        await repo.finish_index_generation(
            kb_id="kb-test",
            file_id="file-test",
            generation=generation,
            chunk_count=1,
            token_count=2,
            processing_job_id="progress-old",
            processing_owner="old",
        )
    assert (await repo.get_by_file_id("file-test")).active_generation == 1
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": "serving",
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_index": 0,
                "generation": 1,
                "content": "old content",
            },
            {
                "chunk_id": "unpublished",
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_index": 0,
                "generation": generation,
                "content": "new content",
            },
        ]
    )
    assert [c.content for c in await KnowledgeChunkRepository().list_by_file_id("file-test")] == ["old content"]


async def test_cleanup_error_remains_pending_and_concurrent_worker_skips_locked_event(database, monkeypatch):
    """失败保留意图；并发 worker 不能把同一个未完成事件标成成功。"""
    async with database() as session, session.begin():
        session.add(
            KnowledgeProjectionOutbox(
                event_key="event", kb_id="kb-test", aggregate_id="kb-test", generation=1, operation="knowledge_base_deleted"
            )
        )
    monkeypatch.setattr(
        projection_cleanup.knowledge_base,
        "cleanup_deleted_knowledge_base",
        AsyncMock(side_effect=RuntimeError("external unavailable")),
    )
    assert await projection_cleanup.process_knowledge_projections() == []
    async with database() as session:
        event = await session.scalar(select(KnowledgeProjectionOutbox))
        assert event.status == "pending" and event.last_error == "external unavailable"
    started, release = asyncio.Event(), asyncio.Event()

    async def cleanup(kb_id):
        """暂停当前 worker，验证另一个 worker 的 SKIP LOCKED。"""
        started.set()
        await release.wait()
        async with database() as session, session.begin():
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))

    monkeypatch.setattr(projection_cleanup.knowledge_base, "cleanup_deleted_knowledge_base", cleanup)
    running = asyncio.create_task(projection_cleanup.process_knowledge_projections())
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert await projection_cleanup.process_knowledge_projections() == []
        release.set()
        assert await running == ["event"]
        async with database() as session:
            event = await session.scalar(select(KnowledgeProjectionOutbox))
            assert event.status == "applied" and event.last_error is None
            assert await session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0
        assert await projection_cleanup.process_knowledge_projections() == []
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)


async def test_cleanup_waits_for_late_index_write_before_marking_applied(database, monkeypatch):
    """暂停真实入口的写入，删除立即隐藏，清理等待旧写入完成并最终移除 Chunk。"""
    await _file()
    started, release = asyncio.Event(), asyncio.Event()

    async def write(kb_id, file_id):
        """仅替换外部编码阶段，最终 PG 写入仍为真实 repository。"""
        started.set()
        await release.wait()
        await KnowledgeChunkRepository().batch_upsert(
            [
                {
                    "chunk_id": "late",
                    "kb_id": kb_id,
                    "file_id": file_id,
                    "chunk_index": 0,
                    "generation": 1,
                    "content": "late write",
                }
            ]
        )

    kb = object.__new__(MilvusKB)
    monkeypatch.setattr(kb, "_index_file", write)

    async def delete_chunks(kb_id, file_id, generation):
        """核对真实 PG 删除。"""
        await KnowledgeChunkRepository().delete_by_file_id(file_id)

    monkeypatch.setattr(
        projection_cleanup.knowledge_base,
        "get_cleanup_executor",
        AsyncMock(return_value=SimpleNamespace(delete_file_chunks_only=delete_chunks, cleanup_file_resources=AsyncMock())),
    )
    writer = asyncio.create_task(kb.index_file("kb-test", "file-test"))
    cleaner = None
    try:
        await asyncio.wait_for(started.wait(), 5)
        key = await KnowledgeFileRepository().mark_deleted(kb_id="kb-test", file_id="file-test")
        assert await KnowledgeFileRepository().get_by_file_id("file-test") is None
        cleaner = asyncio.create_task(projection_cleanup.process_knowledge_projections())
        async with asyncio.timeout(5):
            while True:
                async with database() as session:
                    waiting = await session.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted AND "
                            "objid=((hashtextextended('knowledge-projection:kb-test',0) & 4294967295)::oid))"
                        )
                    )
                    assert await session.scalar(select(KnowledgeProjectionOutbox.status)) == "pending"
                if waiting:
                    break
                await asyncio.sleep(0.01)
        release.set()
        await writer
        assert await cleaner == [key]
        async with database() as session:
            assert await session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
            assert await session.scalar(select(KnowledgeProjectionOutbox.status)) == "applied"
    finally:
        release.set()
        await asyncio.gather(writer, *([cleaner] if cleaner else []), return_exceptions=True)


@pytest.mark.parametrize("batch", [False, True])
async def test_http_delete_does_not_remove_original_before_tombstone_commit(database, monkeypatch, tmp_path, batch):
    """HTTP 删除的 owning transaction 失败时，原始对象与可见文件都保留。"""
    repo = KnowledgeFileRepository()
    await _file()
    original = tmp_path / "raw.md"
    original.write_text("original bytes")

    async def remove_object(*args, **kwargs):
        """模拟真实存储副作用，以文件内容作为独立 oracle。"""
        original.unlink(missing_ok=True)

    async def get_meta(*args):
        """提供原始 MinIO URL，旧路由会据此提前删对象。"""
        return {"meta": {"is_folder": False, "path": "http://minio:9000/documents/kb-test/raw.md"}}

    async def fail_commit(*args):
        """真实执行删除 SQL，但在 owning transaction 退出前模拟提交失败。"""

        @asynccontextmanager
        async def context():
            """异常让真实 PG transaction 回滚。"""
            async with database() as session, session.begin():
                yield session
                raise RuntimeError("simulated owning transaction failure")

        with monkeypatch.context() as scoped:
            scoped.setattr(files.pg_manager, "get_async_session_context", context)
            await repo.mark_deleted(kb_id="kb-test", file_id="file-test")

    monkeypatch.setattr(
        knowledge_router,
        "get_minio_client",
        lambda: SimpleNamespace(
            adelete_file=remove_object,
            KB_BUCKETS={"parsed": "parsed"},
        ),
    )
    from yuxi.api.errors import domain_error_handler
    from yuxi.modules.knowledge import runtime
    from yuxi.shared.errors import DomainError

    monkeypatch.setattr(
        runtime,
        "knowledge_base",
        SimpleNamespace(
            get_file_basic_info=get_meta,
            delete_file=fail_commit,
        ),
    )
    monkeypatch.setattr(knowledge_router, "_ensure_knowledge_base_supports_documents", AsyncMock())
    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    app.add_exception_handler(DomainError, domain_error_handler)
    app.dependency_overrides[knowledge_router.require_knowledge_base_manage] = lambda: SimpleNamespace(role="superadmin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.request(
            "DELETE",
            "/api/knowledge/knowledge-bases/kb-test/documents/" + ("batch" if batch else "file-test"),
            **({"json": ["file-test"]} if batch else {}),
        )
    assert response.status_code == (400 if batch else 500)
    assert original.read_text() == "original bytes"
    assert await repo.get_by_file_id("file-test") is not None
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeProjectionOutbox)) == 0


async def test_tombstones_are_absent_from_tree_names_hashes_and_counts(database):
    """删除提交后树、计数和重复文件检查都不得复活 tombstone。"""
    repo = KnowledgeFileRepository()
    await repo.upsert("folder", {"kb_id": "kb-test", "filename": "folder", "is_folder": True})
    await repo.upsert(
        "child",
        {
            "kb_id": "kb-test",
            "filename": "child.md",
            "parent_id": "folder",
            "content_hash": "hash",
        },
    )
    assert len((await repo.list_documents(kb_id="kb-test", parent_id="folder"))[0]) == 1
    assert await repo.exists_by_filename(kb_id="kb-test", filename="folder/child.md")
    await repo.mark_deleted(kb_id="kb-test", file_id="child")
    assert await repo.list_documents(kb_id="kb-test", parent_id="folder") == ([], 0)
    assert await repo.count_children_by_parent_ids(kb_id="kb-test", parent_ids=["folder"]) == {}
    from yuxi.modules.knowledge.repositories.directories import DirectoryRepository

    async with database() as observer:
        assert await DirectoryRepository().named(observer, "kb-test", "folder", "child.md") is None
    assert await repo.list_file_ids_by_filename_contains(kb_id="kb-test", filename_pattern="child") == []
    assert not await repo.exists_by_filename(kb_id="kb-test", filename="folder/child.md")
    await repo.mark_deleted(kb_id="kb-test", file_id="folder")
    assert await repo.list_documents(kb_id="kb-test", parent_id=None) == ([], 0)
    assert await repo.count_all() == 0


async def test_graph_pending_candidates_and_counts_use_only_visible_active_generation(database):
    """building、旧代次和 tombstone 不能成为图谱构建候选或进度总数。"""
    repo = KnowledgeFileRepository()
    await _file()
    generation = await repo.begin_index_generation(kb_id="kb-test", file_id="file-test")
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_id": "old",
                "generation": 1,
                "chunk_index": 0,
                "content": "old",
            },
            {
                "kb_id": "kb-test",
                "file_id": "file-test",
                "chunk_id": "current",
                "generation": generation,
                "chunk_index": 0,
                "content": "current",
            },
        ]
    )
    chunks = KnowledgeChunkRepository()
    assert [c.chunk_id for c in await chunks.list_graph_pending_by_kb_id("kb-test", 10)] == ["old"]
    assert await chunks.count_graph_pending_by_kb_id("kb-test") == 1
    await repo.finish_index_generation(kb_id="kb-test", file_id="file-test", generation=generation, chunk_count=1, token_count=1)
    assert [c.chunk_id for c in await chunks.list_graph_pending_by_kb_id("kb-test", 10)] == ["current"]
    assert await chunks.count_by_kb_id("kb-test") == 1
    await repo.mark_deleted(kb_id="kb-test", file_id="file-test")
    assert await chunks.list_graph_pending_by_kb_id("kb-test", 10) == []
    assert await chunks.count_by_kb_id("kb-test") == 0
    assert await chunks.count_graph_extraction_statuses_by_kb_id("kb-test") == {
        "pending": 0,
        "succeeded": 0,
        "failed": 0,
    }


async def test_graph_parallel_vector_cancel_drains_both_branches_before_cleanup(database, monkeypatch, tmp_path):
    """真实图谱并行循环取消后，PG 清理等待较慢写分支落地。"""
    from yuxi.modules.knowledge.graphs import service as graph_module

    await _file()
    started, release = threading.Event(), threading.Event()
    entity_done = asyncio.Event()
    artifact = tmp_path / "triple-vector"

    def write_sync():
        """产生可回读的晚写外部对象。"""
        started.set()
        assert release.wait(10)
        artifact.write_text("late triple")

    async def upsert(*, record_type, **kwargs):
        """entity 分支快速完成，triple 分支停在不可取消的 SDK。"""
        if record_type == "triple":
            await await_io(asyncio.to_thread(write_sync))

    async def mark(*, record_type, **kwargs):
        """标识快速分支已经完全结束。"""
        if record_type == "entity":
            entity_done.set()

    graph_repo = SimpleNamespace(
        claim_vector_records=AsyncMock(side_effect=lambda **kwargs: ("owner", [{"id": kwargs["record_type"]}])),
        mark_vector_records_indexed=mark,
        mark_vector_records_failed=AsyncMock(),
        count_vector_statuses_by_kb_id=AsyncMock(return_value={"pending": 2, "processing": 0, "failed": 0}),
        finalize_graph_indexed_chunks=AsyncMock(),
    )
    chunks = SimpleNamespace(
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
        list_graph_pending_by_kb_id=AsyncMock(return_value=[]),
    )
    service = KnowledgeGraphService(
        chunk_repo=chunks, graph_repo=graph_repo, graph_vector_store=SimpleNamespace(upsert_graph_records=upsert)
    )
    monkeypatch.setattr(
        service,
        "_get_milvus_kb",
        AsyncMock(return_value=SimpleNamespace(additional_params={}, embedding_model_spec=None)),
    )
    monkeypatch.setattr(service, "_get_locked_config", lambda params: {"extractor_type": "llm"})
    monkeypatch.setattr(service, "_runtime_extractor_options", lambda config: {})
    monkeypatch.setattr(service, "_get_worker_count", lambda config: 1)
    monkeypatch.setattr(graph_module.GraphExtractorFactory, "create", lambda *args: SimpleNamespace())

    async def cleanup(*args, **kwargs):
        """清理真实文件产物。"""
        artifact.unlink(missing_ok=True)

    monkeypatch.setattr(
        projection_cleanup.knowledge_base,
        "get_cleanup_executor",
        AsyncMock(
            return_value=SimpleNamespace(
                delete_file_chunks_only=cleanup,
                cleanup_file_resources=AsyncMock(),
            )
        ),
    )
    writer = asyncio.create_task(service.build_pending_chunks("kb-test"))
    cleaner = None
    try:
        await asyncio.wait_for(entity_done.wait(), 5)
        async with asyncio.timeout(5):
            while not started.is_set():
                await asyncio.sleep(0.01)
        writer.cancel()
        key = await KnowledgeFileRepository().mark_deleted(kb_id="kb-test", file_id="file-test")
        cleaner = asyncio.create_task(projection_cleanup.process_knowledge_projections())
        async with asyncio.timeout(5):
            while True:
                async with database() as session:
                    waiting = await session.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted "
                            "AND objid=((hashtextextended('knowledge-projection:kb-test',0) & 4294967295)::oid))"
                        )
                    )
                if waiting:
                    break
                await asyncio.sleep(0.01)
        assert not writer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writer
        assert await cleaner == [key]
        assert not artifact.exists()
    finally:
        release.set()
        await asyncio.gather(writer, *([cleaner] if cleaner else []), return_exceptions=True)


async def test_cancelled_collection_writer_keeps_lock_until_io_settles(database, monkeypatch, tmp_path):
    """取消集合创建时，清理必须等待实际 I/O 完成。"""
    await _file()
    started, release = threading.Event(), threading.Event()
    artifact = tmp_path / "external-object"

    def write_sync(*args):
        """模拟不能由协程取消的 SDK 写入，并产生独立文件 oracle。"""
        started.set()
        assert release.wait(10)
        artifact.write_text("late write")

    executor = object.__new__(MilvusKB)
    monkeypatch.setattr(executor, "_create_kb_instance_sync", write_sync)

    async def index(*args, **kwargs):
        """走真实集合创建边界。"""
        await executor._create_kb_instance("kb-test", None)

    monkeypatch.setattr(executor, "_index_file", index)
    write_operation = executor.index_file("kb-test", "file-test")

    async def cleanup(*args, **kwargs):
        """真实删除 late write 的产物。"""
        artifact.unlink(missing_ok=True)

    monkeypatch.setattr(
        projection_cleanup.knowledge_base,
        "get_cleanup_executor",
        AsyncMock(
            return_value=SimpleNamespace(
                delete_file_chunks_only=cleanup,
                cleanup_file_resources=AsyncMock(),
            )
        ),
    )
    writer = asyncio.create_task(write_operation)
    cleaner = None
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                await asyncio.sleep(0.01)
        writer.cancel()
        key = await KnowledgeFileRepository().mark_deleted(kb_id="kb-test", file_id="file-test")
        cleaner = asyncio.create_task(projection_cleanup.process_knowledge_projections())
        async with asyncio.timeout(5):
            while True:
                async with database() as session:
                    waiting = await session.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted "
                            "AND objid=((hashtextextended('knowledge-projection:kb-test',0) & 4294967295)::oid))"
                        )
                    )
                if waiting:
                    break
                await asyncio.sleep(0.01)
        assert not writer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writer
        assert await cleaner == [key]
        assert not artifact.exists()
        async with database() as session:
            assert await session.scalar(select(KnowledgeProjectionOutbox.status)) == "applied"
    finally:
        release.set()
        await asyncio.gather(writer, *([cleaner] if cleaner else []), return_exceptions=True)


async def test_vector_projection_claim_and_finalize_chunk(database):
    """真实向量仓储记录状态被领取、标记并完成 chunk 投影。"""
    suffix = uuid4().hex
    kb_id = f"pytest_vector_{suffix}"
    file_id = f"file_{suffix}"
    chunk_id = f"chunk_{suffix}"
    entity_id = f"entity_{suffix}"
    repo = KnowledgeGraphRepository()

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id=kb_id, name="vector projection", kb_type="milvus"))
            await session.flush()
            session.add(KnowledgeFile(file_id=file_id, kb_id=kb_id, filename="test.md"))
            await session.flush()
            session.add(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="entity",
                    graph_structure_indexed=True,
                    graph_indexed=False,
                )
            )
            session.add(
                KnowledgeGraphEntity(
                    entity_id=entity_id,
                    kb_id=kb_id,
                    normalized_name="entity",
                    label="Entity",
                    name="entity",
                    vector_status="pending",
                )
            )
            await session.flush()
            session.add(KnowledgeGraphEntityMention(entity_id=entity_id, kb_id=kb_id, file_id=file_id, chunk_id=chunk_id))

        token, records = await repo.claim_vector_records(
            kb_id=kb_id,
            record_type="entity",
            limit=10,
            lease_seconds=300,
        )

        assert records == [{"id": entity_id, "content": "entity"}]
        await repo.mark_vector_records_indexed(record_type="entity", record_ids=[entity_id], lock_token=token)
        assert await repo.finalize_graph_indexed_chunks(kb_id) == 1

        async with pg_manager.get_async_session_context() as session:
            chunk = await session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunk_id))
            entity = await session.scalar(select(KnowledgeGraphEntity).where(KnowledgeGraphEntity.entity_id == entity_id))
            assert chunk.graph_indexed is True
            assert entity.vector_status == "indexed"
            assert entity.vector_lock_token is None
    finally:
        async with pg_manager.get_async_session_context() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            if kb is not None:
                await session.delete(kb)


async def test_chunk_extraction_details_return_recent_failure_samples(database):
    """真实仓储返回有序失败详情，并在成功后清空失败样本。"""
    suffix = uuid4().hex
    kb_id = f"pytest_extraction_{suffix}"
    file_id = f"file_{suffix}"
    chunk_id = f"chunk_{suffix}"
    recent_chunk_id = f"recent_chunk_{suffix}"
    repo = KnowledgeChunkRepository()

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id=kb_id, name="extraction details", kb_type="milvus"))
            await session.flush()
            session.add(KnowledgeFile(file_id=file_id, kb_id=kb_id, filename="test.md"))
            await session.flush()
            session.add(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="failed extraction sample",
                )
            )
            session.add(
                KnowledgeChunk(
                    chunk_id=recent_chunk_id,
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=1,
                    content="recent failed extraction sample",
                )
            )

        await repo.mark_graph_extraction_failed(chunk_id, 3, "model timeout")
        await repo.mark_graph_extraction_failed(recent_chunk_id, 1, "invalid response")

        counts = await repo.count_graph_extraction_statuses_by_kb_id(kb_id)
        samples = await repo.list_graph_extraction_failed_samples(kb_id, limit=10)
        assert counts == {"pending": 0, "succeeded": 0, "failed": 2}
        assert [sample["chunk_id"] for sample in samples] == [recent_chunk_id, chunk_id]
        assert samples[1]["file_id"] == file_id
        assert samples[1]["content"] == "failed extraction sample"
        assert samples[1]["details"]["status"] == "failed"
        assert samples[1]["details"]["attempt_count"] == 3
        assert samples[1]["details"]["last_error"] == "model timeout"
        assert samples[1]["details"]["last_attempt_at"]

        await repo.mark_graph_extraction_pending(chunk_id)
        await repo.update_extraction_result(
            chunk_id,
            {"entities": [], "relations": [], "metadata": {"extractor_type": "llm"}},
            2,
        )
        await repo.mark_graph_extraction_pending(recent_chunk_id)
        await repo.update_extraction_result(
            recent_chunk_id,
            {"entities": [], "relations": [], "metadata": {"extractor_type": "llm"}},
            1,
        )

        counts = await repo.count_graph_extraction_statuses_by_kb_id(kb_id)
        assert counts == {"pending": 0, "succeeded": 2, "failed": 0}
        assert await repo.list_graph_extraction_failed_samples(kb_id) == []
    finally:
        async with pg_manager.get_async_session_context() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            if kb is not None:
                await session.delete(kb)
