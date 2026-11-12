"""文档撤销的真实 PostgreSQL 准入、恢复归属与失败发布。"""

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from test.integration.graphs.test_file_graph_clear import graph_data as graph_data
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.modules.background_jobs import repository as job_repository
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.repositories import bases, files, graphs
from yuxi.modules.knowledge.services import document_actions, imports
from yuxi.modules.knowledge.services import document_resets as resets
from yuxi.shared.errors import StateConflictError
from yuxi.workers import background_jobs as worker
from yuxi.workers.background_job_context import BackgroundJobContext

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def document_data(graph_data, monkeypatch):
    """隔离作业和文件事务，复用真实图与分块，真实向量集合由完整 E2E 验证。"""
    manager = graphs.pg_manager
    for module in (job_repository, files, document_actions, resets, imports):
        monkeypatch.setattr(module, "pg_manager", manager)
    monkeypatch.setattr(resets, "dispatch_job", AsyncMock())
    monkeypatch.setattr(document_actions, "dispatch_job", AsyncMock())
    monkeypatch.setattr(worker, "_publish_pending_after_slot_release", AsyncMock())
    executor = MilvusKB.__new__(MilvusKB)
    executor.graph_service = graph_data.service
    executor._get_existing_milvus_collection = AsyncMock(return_value=None)
    monkeypatch.setattr(resets.knowledge_base, "get_cleanup_executor", AsyncMock(return_value=executor))
    async with graph_data.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE knowledge_files SET generation=1, markdown_file='http://minio:9000/parsed/test/document.md', "
                "path='http://minio:9000/documents/test/source.docx', chunk_count=1, token_count=1 WHERE file_id='b'"
            )
        )
    graph_data.executor = executor
    try:
        yield graph_data
    finally:
        await close_async_redis_client()


async def _claim_reset(data, action="clear_index"):
    """走真实登记和租约 claim，避免模拟执行归属。"""
    response = await resets.submit_document_reset(data.kb_id, "b", action=action, operator_id="operator")
    job, claimed = await job_repository.BackgroundJobRepository().claim(response["job_id"], worker_id="reset-owner", lease_seconds=60)
    assert claimed
    return BackgroundJobContext(job.id, job.worker_id, job.payload)


async def _finish_failed(context):
    """通过正式终态事务调用领域失败 hook。"""
    return await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="failed",
        message="failed",
        error="injected failure",
        before_finish=partial(resets.fail_document_reset, error="injected failure"),
    )


@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_rejects_non_object_reset_payload_and_reaches_terminal(document_data, cancel):
    """持久化的损坏 JSON 不能让执行或取消回调阻止作业收敛。"""
    data = document_data
    response = await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")
    repo = job_repository.BackgroundJobRepository()
    async with graphs.pg_manager.get_async_session_context() as session:
        job = await session.get(BackgroundJobRecord, response["job_id"])
        job.payload = ["invalid"]
    if cancel:
        await repo.request_cancel(response["job_id"])
    await worker.process_background_job({"worker_id": "isolated-worker"}, response["job_id"])
    job = await repo.get_by_id(response["job_id"])
    assert job.status == ("cancelled" if cancel else "failed")
    assert job.payload == ["invalid"]
    if not cancel:
        assert "文档撤销任务范围无效" in job.error


@pytest.mark.parametrize("action", [None, "invalid"])
async def test_invalid_reset_action_discards_terminal_content(document_data, action):
    """损坏的动作字段不能让失败任务留下正文，同时保留其他诊断数据。"""
    data = document_data
    response = await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")
    repo = job_repository.BackgroundJobRepository()
    payload = {"kb_id": data.kb_id, "file_id": "b", "content": "private draft", "marker": "retained"}
    if action is not None:
        payload["action"] = action
    async with graphs.pg_manager.get_async_session_context() as session:
        job = await session.get(BackgroundJobRecord, response["job_id"])
        job.payload = payload
    assert (await repo.get_by_id(response["job_id"])).payload == payload
    await worker.process_background_job({"worker_id": "isolated-worker"}, response["job_id"])
    job = await repo.get_by_id(response["job_id"])
    assert job.status == "failed" and "文档撤销任务范围无效" in job.error
    assert "content" not in job.payload and job.payload["marker"] == "retained"
    assert job.payload.get("action") == action


@pytest.mark.parametrize("job_type", ["knowledge_parse", "knowledge_index", "knowledge_ingest", "knowledge_graph_index"])
@pytest.mark.parametrize("job_status", ["pending", "running"])
async def test_reset_rejects_all_preexisting_document_intents(document_data, job_type, job_status):
    """批量待处理、导入两阶段和图谱的持久意图都先于撤销，不创建第二个任务。"""
    data = document_data
    async with graphs.pg_manager.get_async_session_context() as session:
        session.add(
            BackgroundJobRecord(
                id=uuid4().hex,
                name="existing",
                type=job_type,
                status=job_status,
                payload={"kb_id": data.kb_id, "scope": "pending"},
            )
        )
    with pytest.raises(StateConflictError, match="排队或运行"):
        await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM background_jobs")) == 1
        assert await conn.scalar(text("SELECT active_generation FROM knowledge_files WHERE file_id='b'")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1


@pytest.mark.parametrize("history_action", ["prune", "delete_terminal", "delete_all"])
async def test_failed_reset_withdraws_reads_and_survives_job_history_deletion(document_data, history_action):
    """部分清理先撤销读取；正式作业历史回收后，文件动作事实仍阻止绕过并支持同动作重试。"""
    data = document_data
    context = await _claim_reset(data)
    data.executor._get_existing_milvus_collection.side_effect = RuntimeError("vector cleanup unavailable")
    with pytest.raises(RuntimeError, match="vector cleanup"):
        await resets.run_document_reset(context)
    assert await _finish_failed(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.status, file.generation, file.active_generation, file.chunk_count) == ("indexed", 2, 2, 0)
    assert file.projection_status == "clear_index"
    assert file.markdown_file and "重试" in file.error_message
    assert await data.service.chunk_repo.list_by_file_id("b") == []
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1
    repo = job_repository.BackgroundJobRepository()
    if history_action == "prune":
        assert await repo.prune_terminal(keep=0) == [context.job_id]
    elif history_action == "delete_terminal":
        assert await repo.delete_terminal(context.job_id)
    else:
        await repo.delete_all()
    assert await repo.get_by_id(context.job_id) is None
    for action in ("clear_parse", "reparse"):
        with pytest.raises(StateConflictError, match="清除入库数据尚未完成"):
            await resets.submit_document_reset(data.kb_id, "b", action=action, operator_id="operator")
    with pytest.raises(StateConflictError, match="清除入库数据尚未完成"):
        await document_actions.submit_document_action(
            kb_id=data.kb_id,
            file_ids=["b"],
            params={},
            operator_id="operator",
            kb_info=SimpleNamespace(name="kb"),
            action="index",
        )
    with pytest.raises(StateConflictError, match="清除入库数据尚未完成"):
        await files.KnowledgeFileRepository().update_fields(file_id="b", data={"processing_params": {"ocr": "changed"}})
    with pytest.raises(StateConflictError, match="清除入库数据尚未完成"):
        await files.KnowledgeFileRepository().update_fields_if_status(
            kb_id=data.kb_id,
            file_id="b",
            allowed_statuses={"indexed"},
            data={"status": "indexing"},
        )
    assert await files.KnowledgeFileRepository().update_fields_if_status(
        kb_id=data.kb_id,
        file_id="b",
        allowed_statuses={"indexed"},
        data={"status": "indexing"},
        processing_job_id=context.job_id,
        processing_owner=context.worker_id,
    ) is None
    unchanged = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert unchanged.status == "indexed" and unchanged.projection_status == "clear_index"
    with pytest.raises(StateConflictError):
        await data.service.reconcile_vectors(data.kb_id, all_vectors=True)
    await document_actions.submit_document_action(
        kb_id=data.kb_id,
        file_ids=["a"],
        params={},
        operator_id="operator",
        kb_info=SimpleNamespace(name="kb"),
        action="index",
    )
    await job_repository.BackgroundJobRepository().delete_all()
    data.executor._get_existing_milvus_collection.side_effect = None
    retry = await _claim_reset(data)
    await resets.run_document_reset(retry)
    restored = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (restored.status, restored.generation, restored.active_generation, restored.chunk_count) == ("parsed", 2, 2, 0)
    assert restored.processing_job_id is None and restored.error_message is None and restored.projection_status == "pending"
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 0


async def test_active_reset_blocks_new_parse_index_graph_and_ingest_registration(document_data):
    """同一短锁内活动撤销阻止后来的所有处理登记，文件和作业回读无额外写入。"""
    data = document_data
    await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")
    for action in ("parse", "index"):
        with pytest.raises(StateConflictError, match="正在撤销"):
            await document_actions.submit_document_action(
                kb_id=data.kb_id,
                file_ids=["a"],
                params={},
                operator_id="operator",
                kb_info=SimpleNamespace(name="kb"),
                action=action,
            )
    with pytest.raises(StateConflictError, match="正在撤销"):
        await document_actions.submit_knowledge_job(
            name="graph",
            job_type="knowledge_graph_index",
            payload={"kb_id": data.kb_id, "action": "build"},
        )
    with pytest.raises(StateConflictError, match="正在撤销"):
        await imports._register_prepared(
            kb_id=data.kb_id,
            prepared=[],
            params={},
            operator_id="operator",
            identity="import",
            fingerprint="fp",
            process=True,
        )
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM background_jobs")) == 1
        assert await conn.scalar(text("SELECT active_generation FROM knowledge_files WHERE file_id='b'")) == 1


@pytest.mark.parametrize("deleted_target", ["file", "kb"])
async def test_worker_reset_failure_after_target_deletion_commits_terminal_job(document_data, monkeypatch, deleted_target):
    """文件删除阻止最终发布，知识库删除后的失败 hook 安全提交作业终态。"""
    data = document_data
    response = await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")

    async def delete_then_fail(*_args, **_kwargs):
        """在外部副作用边界制造知识库 tombstone 与清理失败交错。"""
        if deleted_target == "kb":
            await bases.KnowledgeBaseRepository().mark_deleted(data.kb_id)
            raise RuntimeError("deleted during cleanup")
        await files.KnowledgeFileRepository().mark_deleted(kb_id=data.kb_id, file_id="b")

    monkeypatch.setattr(data.executor, "delete_file_chunks_only", delete_then_fail)
    await worker.process_background_job({"worker_id": "isolated-worker"}, response["job_id"])
    async with data.engine.connect() as conn:
        job = (await conn.execute(text("SELECT status,error FROM background_jobs WHERE id=:id"), {"id": response["job_id"]})).one()
        assert job.status == "failed" and job.error
        if deleted_target == "kb":
            assert "deleted during cleanup" in job.error
        else:
            assert "文件不存在或不属于该知识库" in job.error
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_files WHERE deleted_at IS NULL")) == (
            1 if deleted_target == "file" else 0
        )
        file = (await conn.execute(text("SELECT status,active_generation,deleted_at FROM knowledge_files WHERE file_id='b'"))).one()
        assert file.status == "deleted" and file.active_generation == 2 and file.deleted_at is not None


@pytest.mark.parametrize("cancelled", [False, True])
async def test_parse_clear_drains_all_prefix_io_before_releasing_projection_lock(document_data, monkeypatch, cancelled):
    """单侧删除失败或取消时，另一删除线程结束之前不得让新投影写入越过。"""
    import asyncio
    import threading

    from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

    data = document_data
    entered, released = asyncio.Event(), threading.Event()
    ended = threading.Event()

    def blocked_delete():
        """用真实线程模拟 MinIO 前缀删除，不能由协程取消来停止。"""
        try:
            assert released.wait(5), "删除线程没有被释放"
        finally:
            ended.set()

    async def delete_prefix(bucket, _prefix):
        """图片删除被阻塞，Markdown 删除已经失败。"""
        if bucket == "images":
            entered.set()
            await asyncio.to_thread(blocked_delete)
        else:
            raise RuntimeError("parsed prefix failed")

    monkeypatch.setattr(
        resets,
        "get_minio_client",
        lambda: SimpleNamespace(
            KB_BUCKETS={"images": "images", "parsed": "parsed"},
            adelete_objects_by_prefix=delete_prefix,
        ),
    )
    context = await _claim_reset(data, "clear_parse")
    reset = asyncio.create_task(resets.run_document_reset(context))
    acquired = asyncio.Event()

    async def next_writer():
        """真实共享投影锁直接证明旧 I/O 没有越过释放边界。"""
        async with knowledge_projection_lock(data.kb_id, shared=True):
            acquired.set()

    writer = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        file = await files.KnowledgeFileRepository().get_by_file_id("b")
        assert file.markdown_file is None and file.active_generation == 2
        assert await data.service.chunk_repo.list_by_file_id("b") == []
        writer = asyncio.create_task(next_writer())
        if cancelled:
            reset.cancel()
        await asyncio.sleep(0.05)
        assert not reset.done() and not acquired.is_set() and not ended.is_set()
    finally:
        released.set()
        expected = asyncio.CancelledError if cancelled else RuntimeError
        with pytest.raises(expected):
            await reset
        if writer is not None:
            await asyncio.wait_for(writer, timeout=5)
    assert ended.is_set() and acquired.is_set()
    assert await _finish_failed(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.projection_status == "clear_parse" and file.markdown_file is None
    assert file.status == "indexed" and "重试" in file.error_message


async def test_reset_rejects_expired_owner_after_waiting_for_projection_lock(document_data):
    """真实租约已过期的撤销在拿锁后退出，不能先撤销读取再发现失去执行权。"""
    import asyncio

    from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

    data = document_data
    context = await _claim_reset(data)
    async with knowledge_projection_lock(data.kb_id, shared=True):
        reset = asyncio.create_task(resets.run_document_reset(context))
        await asyncio.sleep(0.05)
        assert not reset.done()
        async with data.engine.begin() as conn:
            await conn.execute(
                text("UPDATE background_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                {"id": context.job_id},
            )
    with pytest.raises(asyncio.CancelledError):
        await reset
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.generation, file.active_generation, file.processing_job_id) == (1, 1, None)
    assert file.markdown_file and file.status == "indexed"
    assert len(await data.service.chunk_repo.list_by_file_id("b")) == 1


async def test_same_reset_attempt_resumes_existing_empty_generation(document_data):
    """同一持久意图更换 worker 后复用已撤销的空代次，不因旧 payload 误报版本冲突。"""
    data = document_data
    context = await _claim_reset(data)
    data.executor._get_existing_milvus_collection.side_effect = RuntimeError("interrupted cleanup")
    with pytest.raises(RuntimeError, match="interrupted"):
        await resets.run_document_reset(context)
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE background_jobs SET worker_id='replacement-owner' WHERE id=:id"), {"id": context.job_id})
    data.executor._get_existing_milvus_collection.side_effect = None
    replacement = BackgroundJobContext(context.job_id, "replacement-owner", context.payload)
    await resets.run_document_reset(replacement)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.status, file.generation, file.active_generation) == ("parsed", 2, 2)
    assert file.processing_job_id is None and file.error_message is None


@pytest.mark.parametrize("overrides", [{"action": []}, {"params": []}, {"file_id": ""}, {"generation": True}])
async def test_worker_invalid_reset_payload_reaches_failed_terminal_without_file_changes(document_data, overrides):
    """非法持久范围通过真实 worker 失败收敛，失败 hook 不再访问不可信动作或复活文件。"""
    data = document_data
    job_id = uuid4().hex
    payload = {"kb_id": data.kb_id, "file_id": "b", "action": "clear_parse", "generation": 1, "operator_id": "operator", **overrides}
    async with graphs.pg_manager.get_async_session_context() as session:
        session.add(BackgroundJobRecord(id=job_id, name="invalid reset", type="knowledge_document_reset", payload=payload))
    await worker.process_background_job({"worker_id": "isolated-worker"}, job_id)
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT status FROM background_jobs WHERE id=:id"), {"id": job_id}) == "failed"
        file = (
            await conn.execute(text("SELECT status,generation,active_generation,processing_job_id FROM knowledge_files WHERE file_id='b'"))
        ).one()
        assert tuple(file) == ("indexed", 1, 1, None)
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1


@pytest.mark.parametrize("status", ["parsing", "indexing", "waiting", "unknown", "deleted"])
async def test_reset_rejects_nonstable_status_without_job_or_withdrawal(document_data, status):
    """目录之外的非法或运行态也不能伪装为可撤销文件。"""
    data = document_data
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET status=:status WHERE file_id='b'"), {"status": status})
    with pytest.raises(StateConflictError, match="当前状态"):
        await resets.submit_document_reset(data.kb_id, "b", action="clear_index", operator_id="operator")
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM background_jobs")) == 0
        assert await conn.scalar(text("SELECT active_generation FROM knowledge_files WHERE file_id='b'")) == 1


@pytest.fixture
async def markdown_data(document_data, monkeypatch, tmp_path):
    """正文对象替身只隔离存储，修订与发布使用真实 PostgreSQL 和作业租约。"""
    data = document_data
    source = f"http://minio:9000/parsed/{data.kb_id}/parsed/b/old/document.md"
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET markdown_file=:source WHERE file_id='b'"), {"source": source})
    data.objects = {source: b"old source"}
    old_file = tmp_path / "old.md"
    old_file.write_bytes(b"old source")
    data.object_paths = {source: old_file}
    data.deleted_objects = []

    async def stage(kb_id, file_id, attempt_id, content):
        """使用与真实解析一致的不可变对象路径。"""
        url = f"http://minio:9000/parsed/{kb_id}/parsed/{file_id}/{attempt_id}/document.md"
        data.objects[url] = content.encode()
        path = tmp_path / f"{attempt_id}.md"
        path.write_bytes(content.encode())
        data.object_paths[url] = path
        return url

    async def delete_prefix(bucket, prefix):
        """按对象范围回收，保留所有范围之外的正文。"""
        for key in list(data.objects):
            if key.startswith(f"http://minio:9000/{bucket}/{prefix}"):
                data.deleted_objects.append(key)
                del data.objects[key]
                data.object_paths.pop(key).unlink()

    async def delete_file(bucket, key):
        """只回收指定旧正文。"""
        url = f"http://minio:9000/{bucket}/{key}"
        data.deleted_objects.append(url)
        data.objects.pop(url, None)
        path = data.object_paths.pop(url, None)
        if path is not None:
            path.unlink()

    async def download(*_args, **_kwargs):
        """读取当前持久指针对应的正文。"""
        file = await files.KnowledgeFileRepository().get_by_file_id("b")
        return {"content": data.object_paths[file.markdown_file].read_bytes()}

    data.source = source
    monkeypatch.setattr(data.executor, "save_markdown_artifact", stage)
    monkeypatch.setattr(resets.documents, "get_file_download", download)
    data.client = SimpleNamespace(
        KB_BUCKETS={"parsed": "parsed", "images": "kb-images"}, adelete_objects_by_prefix=delete_prefix, adelete_file=delete_file
    )
    monkeypatch.setattr(resets, "get_minio_client", lambda: data.client)
    monkeypatch.setattr("yuxi.modules.knowledge.base.get_minio_client", lambda: data.client)
    return data


async def _claim_markdown(data, revision, content="new source"):
    """保存登记和租约沿用正式 Owner。"""
    response = await resets.submit_document_reset(
        data.kb_id, "b", action="save_markdown", operator_id="operator", expected_revision=revision, content=content
    )
    job, claimed = await job_repository.BackgroundJobRepository().claim(response["job_id"], worker_id="save-owner", lease_seconds=60)
    assert claimed
    return BackgroundJobContext(job.id, job.worker_id, job.payload)


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled", "cancel_after_start"])
async def test_markdown_job_discards_content_only_on_terminal(markdown_data, outcome, monkeypatch):
    """真实 worker 终态清除正文，待执行记录与任务诊断信息仍完整。"""
    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    response = await resets.submit_document_reset(
        data.kb_id, "b", action="save_markdown", operator_id="operator", expected_revision=revision, content="private draft"
    )
    repo = job_repository.BackgroundJobRepository()
    job = await repo.get_by_id(response["job_id"])
    assert job.status == "pending" and job.payload["content"] == "private draft"
    if outcome == "failed":
        data.executor._get_existing_milvus_collection.side_effect = RuntimeError("cleanup failed")
    elif outcome == "cancelled":
        await repo.request_cancel(job.id)
    elif outcome == "cancel_after_start":
        stage = data.executor.save_markdown_artifact

        async def cancel_during_stage(*args):
            await repo.request_cancel(job.id)
            return await stage(*args)

        monkeypatch.setattr(data.executor, "save_markdown_artifact", cancel_during_stage)
    await worker.process_background_job({"worker_id": "isolated-worker"}, job.id)
    job = await repo.get_by_id(job.id)
    assert job.status == ("cancelled" if outcome == "cancel_after_start" else outcome)
    assert "content" not in job.payload
    assert job.payload["action"] == "save_markdown" and job.payload["file_id"] == "b"
    if outcome in {"success", "cancel_after_start"}:
        file = await files.KnowledgeFileRepository().get_by_file_id("b")
        assert data.objects[file.markdown_file] == b"private draft"
    elif outcome == "failed":
        assert "cleanup failed" in job.error
        assert (await files.KnowledgeFileRepository().get_by_file_id("b")).projection_status == "save_markdown"
    else:
        assert data.objects == {data.source: b"old source"}


async def test_markdown_expired_owner_cannot_discard_until_reconciliation(markdown_data):
    """过期 owner 无权改正文，失租收敛与失败终态原子清理。"""
    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision, "private draft")
    async with data.engine.begin() as conn:
        await conn.execute(
            text("UPDATE background_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"), {"id": context.job_id}
        )
    assert not await _finish_failed(context)
    repo = job_repository.BackgroundJobRepository()
    job = await repo.get_by_id(context.job_id)
    assert job.status == "running" and job.payload["content"] == "private draft"
    await repo.reconcile_expired_leases(before_fail=worker.finalize_job_failure)
    job = await repo.get_by_id(context.job_id)
    assert job.status == "failed" and "content" not in job.payload
    assert "worker_lease_expired" in job.error and job.payload["file_id"] == "b"


async def test_markdown_save_publishes_empty_content_and_rejects_old_revision(markdown_data):
    """人工空正文也是有效保存，发布后旧修订不能覆盖新指针。"""
    data = markdown_data
    before = await resets.read_markdown_for_edit(data.kb_id, "b")
    context = await _claim_markdown(data, before["revision"], "")
    await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.status, file.generation, file.active_generation, file.projection_status) == ("parsed", 2, 2, "pending")
    assert file.markdown_file != data.source and data.objects[file.markdown_file] == b""
    assert data.source not in data.objects
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 0
        assert (
            await conn.scalar(text("SELECT path FROM knowledge_files WHERE file_id='b'")) == "http://minio:9000/documents/test/source.docx"
        )
    assert await _finish_failed(context)  # 终态释放准入，不改变已发布文件。
    with pytest.raises(StateConflictError, match="修订已改变"):
        await _claim_markdown(data, before["revision"])
    assert data.objects[file.markdown_file] == b""


@pytest.mark.parametrize("history_action", ["prune", "delete_terminal", "delete_all"])
async def test_failed_markdown_save_keeps_source_and_retry_generation_after_history_deletion(markdown_data, history_action):
    """下游失败后原正文不丢，持久动作在作业历史删除后仍允许原稿窄恢复。"""
    data = markdown_data
    before = await resets.read_markdown_for_edit(data.kb_id, "b")
    data.executor._get_existing_milvus_collection.side_effect = RuntimeError("storage unavailable")
    for attempt in range(2):
        context = await _claim_markdown(data, before["revision"])
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await resets.run_document_reset(context)
        assert await _finish_failed(context)
        file = await files.KnowledgeFileRepository().get_by_file_id("b")
        assert (file.generation, file.active_generation, file.projection_status, file.markdown_file) == (2, 2, "save_markdown", data.source)
        assert data.objects == {data.source: b"old source"}
        repo = job_repository.BackgroundJobRepository()
        if history_action == "prune":
            await repo.prune_terminal(keep=0)
        elif history_action == "delete_terminal":
            assert await repo.delete_terminal(context.job_id)
        else:
            await repo.delete_all()
        assert await repo.get_by_id(context.job_id) is None
        reopened = await resets.read_markdown_for_edit(data.kb_id, "b")
        assert reopened["content"] == "old source" and reopened["revision"] != before["revision"]
    data.executor._get_existing_milvus_collection.side_effect = None
    context = await _claim_markdown(data, before["revision"], "retry source")
    await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.generation == 2 and data.objects[file.markdown_file] == b"retry source"


async def test_markdown_stage_failure_never_withdraws_source_or_indices(markdown_data, monkeypatch):
    """暂存失败必须发生在空代次与派生产物撤销之前。"""
    data = markdown_data
    before = await resets.read_markdown_for_edit(data.kb_id, "b")
    monkeypatch.setattr(data.executor, "save_markdown_artifact", AsyncMock(side_effect=RuntimeError("stage unavailable")))
    context = await _claim_markdown(data, before["revision"])
    with pytest.raises(RuntimeError, match="stage unavailable"):
        await resets.run_document_reset(context)
    assert await _finish_failed(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.generation, file.active_generation, file.markdown_file, file.chunk_count) == (1, 1, data.source, 1)
    assert file.projection_status != "save_markdown" and data.objects == {data.source: b"old source"}
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1


async def test_markdown_read_failure_is_not_empty_or_chunk_fallback(markdown_data, monkeypatch):
    """持久正文读取失败不得用仍有的分块或空串冒充可靠原文。"""
    from yuxi.shared.errors import OperationFailedError

    data = markdown_data
    monkeypatch.setattr(resets.documents, "get_file_download", AsyncMock(side_effect=RuntimeError("missing object")))
    with pytest.raises(OperationFailedError, match="无法读取现有 Markdown"):
        await resets.read_markdown_for_edit(data.kb_id, "b")
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET markdown_file=NULL,status='uploaded' WHERE file_id='b'"))
    result = await resets.read_markdown_for_edit(data.kb_id, "b")
    assert result["content"] == "" and result["has_parsed_markdown"] is False


async def test_old_reindex_revision_cannot_revive_during_later_failed_save(markdown_data):
    """相同正文重新入库后的旧草稿不能借另一次保存的恢复例外复活。"""
    data = markdown_data
    obsolete = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET generation=2,active_generation=2 WHERE file_id='b'"))
    current = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    data.executor._get_existing_milvus_collection.side_effect = RuntimeError("cleanup failed")
    context = await _claim_markdown(data, current)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await resets.run_document_reset(context)
    assert await _finish_failed(context)
    with pytest.raises(StateConflictError, match="修订已改变"):
        await _claim_markdown(data, obsolete)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.generation == 3 and file.markdown_file == data.source


async def test_published_markdown_remains_success_when_old_object_reclamation_fails(markdown_data):
    """正文发布是成功事实，旧对象回收失败不能把保存任务报告失败。"""
    data = markdown_data
    data.client.adelete_file = AsyncMock(side_effect=RuntimeError("old object unavailable"))
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision)
    result = await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert result["succeeded"] == 1 and file.status == "parsed"
    assert data.objects[file.markdown_file] == b"new source" and data.objects[data.source] == b"old source"


@pytest.mark.parametrize("interruption", ["lease", "file", "kb"])
async def test_markdown_publish_rejects_lost_owner_or_deleted_target_and_reclaims_stage(markdown_data, monkeypatch, interruption):
    """副作用后失租或目标删除不能发布正文，暂存对象在退出投影锁之前被回收。"""
    import asyncio

    from yuxi.shared.errors import ResourceNotFoundError

    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision)
    original = resets.clear_document_derivatives

    async def clear_then_interrupt(*args, **kwargs):
        """在真实下游清理完成后制造发布冲突。"""
        await original(*args, **kwargs)
        if interruption == "lease":
            async with data.engine.begin() as conn:
                await conn.execute(
                    text("UPDATE background_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                    {"id": context.job_id},
                )
        elif interruption == "file":
            await files.KnowledgeFileRepository().mark_deleted(kb_id=data.kb_id, file_id="b")
        else:
            await bases.KnowledgeBaseRepository().mark_deleted(data.kb_id)

    monkeypatch.setattr(resets, "clear_document_derivatives", clear_then_interrupt)
    with pytest.raises(asyncio.CancelledError if interruption == "lease" else ResourceNotFoundError):
        await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b", include_deleted=True)
    assert file.markdown_file == data.source and data.objects == {data.source: b"old source"}
    assert file.generation == 2
    if interruption != "lease":
        assert file.deleted_at is not None and file.status == "deleted"


@pytest.mark.parametrize("failure", ["cancel", "exception"])
async def test_markdown_committed_publish_return_failure_preserves_referenced_object(markdown_data, monkeypatch, failure):
    """发布已提交但调用返回失败时，回收必须以真实正文指针保护新对象。"""
    import asyncio

    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision)
    original = context.run_owned_transaction

    async def commit_then_raise(operation):
        """真实发布事务完成后才抛出返回阶段故障。"""
        result = await original(operation)
        if operation.__name__ == "publish":
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("after commit")
        return result

    monkeypatch.setattr(context, "run_owned_transaction", commit_then_raise)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.status == "parsed" and file.generation == 2 and file.markdown_file != data.source
    assert data.objects[file.markdown_file] == b"new source" and data.source not in data.objects
    assert (await resets.documents.get_file_download(data.kb_id, "b", variant="parsed"))["content"] == b"new source"


async def test_markdown_unknown_publish_confirmation_preserves_real_object(markdown_data, monkeypatch):
    """PG 回读失败时不能把未知发布事实解释成未发布并清理正文。"""
    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision)
    original = files.KnowledgeFileRepository.get_by_file_id

    async def unreadable_confirmation(self, file_id, *, include_deleted=False):
        """只有回收前的权威查询故障，正常结果读取仍可完成。"""
        if include_deleted:
            raise RuntimeError("confirmation unavailable")
        return await original(self, file_id)

    monkeypatch.setattr(files.KnowledgeFileRepository, "get_by_file_id", unreadable_confirmation)
    await resets.run_document_reset(context)
    file = await original(files.KnowledgeFileRepository(), "b")
    assert file.markdown_file != data.source and data.object_paths[file.markdown_file].read_bytes() == b"new source"
    assert data.object_paths[data.source].read_bytes() == b"old source"


@pytest.mark.parametrize("failure", ["cancel", "exception"])
async def test_parse_committed_publish_return_failure_preserves_markdown_and_images(markdown_data, monkeypatch, tmp_path, failure):
    """重新解析依赖的发布已提交时，返回故障也不能回收正文与本次图片。"""
    import asyncio

    data = markdown_data
    async with data.engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET status='uploaded',markdown_file=NULL WHERE file_id='b'"))
    image = tmp_path / "parse-image.png"

    async def parse_source(**_kwargs):
        """解析产生真实图片文件，发布后的异常必须保留它。"""
        image.write_bytes(b"image bytes")
        return "parsed source with image"

    async def cleanup(_kb, _file, attempt_id):
        """错误回收会直接破坏真实文件，独立 oracle 能检出。"""
        image.unlink(missing_ok=True)
        for url, path in list(data.object_paths.items()):
            if f"/{attempt_id}/" in url:
                path.unlink(missing_ok=True)

    original = files.KnowledgeFileRepository.update_fields_if_status

    async def publish_then_raise(self, **kwargs):
        """正式 repository 完成提交后才抛出调用返回阶段错误。"""
        result = await original(self, **kwargs)
        if kwargs["data"].get("status") == "parsed" and result is not None:
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("parse after commit")
        return result

    monkeypatch.setattr("yuxi.modules.documents.service.parse_to_hosted_markdown", parse_source)
    monkeypatch.setattr(files.KnowledgeFileRepository, "update_fields_if_status", publish_then_raise)
    monkeypatch.setattr(data.executor, "_cleanup_parse_attempt", cleanup)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
        await data.executor.parse_file(data.kb_id, "b", additional_params={})
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.status == "parsed" and file.markdown_file != data.source
    assert data.object_paths[file.markdown_file].read_bytes() == b"parsed source with image"
    assert image.read_bytes() == b"image bytes"


@pytest.mark.parametrize("changed_field", ["generation", "markdown_file"])
async def test_markdown_worker_rechecks_revision_before_staging_or_withdrawal(markdown_data, changed_field):
    """提交后来源修订变化时，worker 必须在任何暂存与下游撤销之前拒绝。"""
    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    context = await _claim_markdown(data, revision)
    async with data.engine.begin() as conn:
        if changed_field == "generation":
            await conn.execute(text("UPDATE knowledge_files SET generation=2,active_generation=2 WHERE file_id='b'"))
        else:
            await conn.execute(
                text("UPDATE knowledge_files SET markdown_file=:changed WHERE file_id='b'"), {"changed": data.source + ".new"}
            )
    before_files = set(data.object_paths)
    with pytest.raises(StateConflictError, match="代次已改变|修订已改变"):
        await resets.run_document_reset(context)
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert file.chunk_count == 1 and file.projection_status != "save_markdown"
    assert file.processing_job_id is None
    assert (file.generation, file.active_generation) == ((2, 2) if changed_field == "generation" else (1, 1))
    assert file.markdown_file == (data.source if changed_field == "generation" else data.source + ".new")
    assert set(data.object_paths) == before_files
    assert all(path.exists() for path in data.object_paths.values())
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1


async def test_concurrent_markdown_submissions_persist_only_one_intent(markdown_data):
    """相同修订的并发保存只登记一个意图，另一个冲突且文件未提前变化。"""
    import asyncio

    data = markdown_data
    revision = (await resets.read_markdown_for_edit(data.kb_id, "b"))["revision"]
    outcomes = await asyncio.gather(
        *(
            resets.submit_document_reset(
                data.kb_id, "b", action="save_markdown", operator_id="operator", expected_revision=revision, content=content
            )
            for content in ("draft one", "draft two")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, dict) and outcome["status"] == "queued" for outcome in outcomes) == 1
    assert sum(isinstance(outcome, StateConflictError) for outcome in outcomes) == 1
    file = await files.KnowledgeFileRepository().get_by_file_id("b")
    assert (file.generation, file.active_generation, file.markdown_file, file.chunk_count) == (1, 1, data.source, 1)
    assert data.object_paths[data.source].read_bytes() == b"old source" and len(data.object_paths) == 1
    async with data.engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM background_jobs WHERE type='knowledge_document_reset'")) == 1
        assert await conn.scalar(text("SELECT count(*) FROM knowledge_chunks WHERE file_id='b'")) == 1
