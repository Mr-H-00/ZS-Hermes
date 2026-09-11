"""Milvus Parent-Child 入库流程的失败回滚测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB


class _FakeVersion:
    version_id = "version-new"


class _FakeParentChildRepository:
    """记录 staging、写入和清理调用的伪 repository。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.activation_owner: tuple[str | None, str | None] | None = None
        self.activation_file_update: tuple[int, int, str | None] | None = None
        self.old_version = SimpleNamespace(
            version_id="version-old",
            kb_id="kb-1",
            file_id="file-1",
            embedding_dimension=1024,
            status="active",
        )

    async def get_active_version(self, _kb_id, _file_id):
        """返回带持久化 embedding 维度的旧 active 版本。"""
        return self.old_version

    async def get_version(self, version_id):
        """返回清理流程所需的旧版本持久化事实。"""
        if self.old_version is not None and version_id == self.old_version.version_id:
            return self.old_version
        return None

    async def create_staging_version(self, **_kwargs):
        self.calls.append(("create", "version-new"))
        return _FakeVersion()

    async def batch_insert_parent_chunks(self, version_id, _parents):
        self.calls.append(("parents", version_id))

    async def batch_insert_child_chunks(self, version_id, _children):
        self.calls.append(("children", version_id))

    async def activate_version(
        self,
        version_id,
        *,
        processing_task_id,
        processing_owner,
        chunk_count,
        token_count,
        updated_by,
    ):
        """记录激活使用的 owner 与原子文件终态。"""
        self.activation_owner = (processing_task_id, processing_owner)
        self.activation_file_update = (chunk_count, token_count, updated_by)
        self.calls.append(("activate", version_id))
        if self.old_version is not None:
            self.old_version.status = "superseded"
        file_record = SimpleNamespace(
            result={
                "file_id": "file-1",
                "kb_id": "kb-1",
                "status": "indexed",
                "chunk_count": chunk_count,
                "token_count": token_count,
            }
        )
        return _FakeVersion(), file_record

    async def delete_version(self, version_id):
        self.calls.append(("delete", version_id))
        if self.old_version is not None and version_id == self.old_version.version_id:
            self.old_version = None


class _FakeCollection:
    def __init__(self):
        self.deleted: list[str] = []
        self.flushed = False

    def flush(self):
        self.flushed = True

    def query(self, **_kwargs):
        return []

    def delete(self, expression):
        self.deleted.append(expression)


def _configure_parent_child_flow(monkeypatch, kb, repository, collection):
    """配置 Parent-Child 流程共享测试替身。"""
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", lambda: repository)
    monkeypatch.setattr(
        milvus_module,
        "model_cache",
        SimpleNamespace(get_model_info=lambda _spec: SimpleNamespace(dimension=4)),
    )
    monkeypatch.setattr(
        milvus_module,
        "chunk_markdown_parent_child",
        lambda *_args, **_kwargs: {
            "parents": [
                {
                    "parent_id": "parent-1",
                    "parent_index": 0,
                    "parent_text": "parent",
                    "token_count": 7,
                }
            ],
            "children": [
                {
                    "child_id": "child-1",
                    "parent_id": "parent-1",
                    "child_index": 0,
                    "child_text": "child",
                    "token_count": 11,
                }
            ],
        },
    )
    monkeypatch.setattr(kb, "_get_or_create_child_collection", lambda *_args, **_kwargs: _async_value(collection))
    monkeypatch.setattr(kb, "_get_existing_child_collection", lambda _dimension: collection)
    monkeypatch.setattr(kb, "_read_markdown_from_minio", lambda *_args: _async_value("markdown"))
    monkeypatch.setattr(kb, "_file_record_to_meta", lambda record: record.result)

    class LiveTaskRepository:
        """让既有流程测试保持有效的 owner lease。"""

        async def check_control(self, _task_id, *, worker_id):
            """返回仍由当前测试 worker 持有的 lease。"""
            del worker_id
            return True, False

    monkeypatch.setattr(milvus_module, "TaskRepository", LiveTaskRepository)

    class NoopGraphService:
        """模拟无需删除图谱数据的旧版本。"""

        async def delete_parent_child_version_graph(self, _kb_id, _file_id, _version_id):
            """接受幂等图谱清理。"""

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        NoopGraphService,
    )


@pytest.mark.asyncio
async def test_parent_child_index_rolls_back_staging_and_children_before_activation(monkeypatch):
    """Milvus 回读失败时应清理新版本，且绝不调用 activate。"""
    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    collection = _FakeCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))

    async def embed(_texts):
        return [[0.1, 0.2, 0.3, 0.4]]

    with pytest.raises(RuntimeError, match="read back"):
        await kb._index_parent_child_file(
            kb_id="kb-1",
            file_id="file-1",
            file_meta={"markdown_file": "minio://parsed/file-1.md", "filename": "a.md"},
            params={"indexing_path": "parent_child", "chunk_preset_id": "naive", "parent_child": {"enabled": True}},
            embedding_model_spec="provider:model",
            embedding_function=embed,
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    assert ("delete", "version-new") in repository.calls
    assert not any(call[0] == "activate" for call in repository.calls)
    assert collection.deleted == ['knowledge_base_id == "kb-1" and version_id == "version-new"']


@pytest.mark.asyncio
async def test_parent_child_sparse_requires_provider_output_and_rolls_back(monkeypatch):
    """启用 BGE-M3 sparse 但 provider 无原生输出时必须失败并清理 staging。"""
    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    collection = _FakeCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)

    async def embed(_texts):
        return [[0.1, 0.2, 0.3, 0.4]]

    with pytest.raises(ValueError, match="provider 未提供 sparse"):
        await kb._index_parent_child_file(
            kb_id="kb-1",
            file_id="file-1",
            file_meta={"markdown_file": "minio://parsed/file-1.md"},
            params={
                "indexing_path": "parent_child",
                "parent_child": {"enabled": True},
                "embedding_features": {"bge_m3_sparse_enabled": True},
            },
            embedding_model_spec="provider:model",
            embedding_function=embed,
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    assert ("delete", "version-new") in repository.calls
    assert not any(call[0] == "activate" for call in repository.calls)


@pytest.mark.asyncio
async def test_parent_child_collection_failure_rolls_back_staging(monkeypatch):
    """child collection 初始化失败也必须清理已创建的 staging 版本。"""
    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    collection = _FakeCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)

    async def fail_collection(*_args, **_kwargs):
        raise RuntimeError("collection unavailable")

    monkeypatch.setattr(kb, "_get_or_create_child_collection", fail_collection)

    with pytest.raises(RuntimeError, match="collection unavailable"):
        await kb._index_parent_child_file(
            kb_id="kb-1",
            file_id="file-1",
            file_meta={"markdown_file": "minio://parsed/file-1.md"},
            params={"indexing_path": "parent_child", "chunk_preset_id": "naive", "parent_child": {"enabled": True}},
            embedding_model_spec="provider:model",
            embedding_function=lambda _texts: _async_value([]),
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    assert repository.calls == [("create", "version-new"), ("delete", "version-new")]


@pytest.mark.asyncio
async def test_parent_child_owner_loss_rolls_back_staging_and_milvus_projection(monkeypatch):
    """激活拒绝旧 owner 后必须清理其 staging 与 Milvus 投影。"""

    class OwnerRejectingRepository(_FakeParentChildRepository):
        """模拟真实 repository 在发布点拒绝已失效 owner。"""

        async def activate_version(
            self,
            version_id,
            *,
            processing_task_id,
            processing_owner,
            chunk_count,
            token_count,
            updated_by,
        ):
            """记录旧 owner 并拒绝版本发布。"""
            del chunk_count, token_count, updated_by
            self.activation_owner = (processing_task_id, processing_owner)
            self.calls.append(("activate", version_id))
            raise asyncio.CancelledError("File processing owner was lost")

    class SuccessfulReadBackCollection(_FakeCollection):
        """模拟已写入且可回读的新版本 Milvus 投影。"""

        def query(self, **_kwargs):
            """返回单条子块以进入版本激活阶段。"""
            return [{"child_id": "child-1"}]

    kb = MilvusKB.__new__(MilvusKB)
    repository = OwnerRejectingRepository()
    collection = SuccessfulReadBackCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(asyncio.CancelledError, match="owner"):
        await kb._index_parent_child_file(
            kb_id="kb-1",
            file_id="file-1",
            file_meta={"markdown_file": "unused", "filename": "a.md"},
            params={"indexing_path": "parent_child", "chunk_preset_id": "naive", "parent_child": {"enabled": True}},
            embedding_model_spec="provider:model",
            embedding_function=lambda _texts: _async_value([[0.1, 0.2, 0.3, 0.4]]),
            processing_task_id="task-old",
            processing_owner="worker-old",
            markdown_content="markdown",
        )

    assert repository.activation_owner == ("task-old", "worker-old")
    assert ("delete", "version-new") in repository.calls
    assert collection.deleted == ['knowledge_base_id == "kb-1" and version_id == "version-new"']


@pytest.mark.parametrize("cancellation_point", ["during_activation", "after_activation"])
@pytest.mark.asyncio
async def test_parent_child_cancellation_keeps_committed_active_projection(monkeypatch, cancellation_point):
    """激活提交期间或之后的取消都不得删除新 active 版本及其 Milvus 行。"""

    activation_entered = asyncio.Event()
    release_activation = asyncio.Event()

    class StatefulRepository(_FakeParentChildRepository):
        """模拟 active 版本切换并拒绝删除 active 版本。"""

        def __init__(self):
            """初始化旧 active 版本。"""
            super().__init__()
            self.active_version_id = "version-old"

        async def activate_version(
            self,
            version_id,
            *,
            processing_task_id,
            processing_owner,
            chunk_count,
            token_count,
            updated_by,
        ):
            """提交新 active 版本。"""
            if cancellation_point == "during_activation":
                activation_entered.set()
                await release_activation.wait()
            activation = await super().activate_version(
                version_id,
                processing_task_id=processing_task_id,
                processing_owner=processing_owner,
                chunk_count=chunk_count,
                token_count=token_count,
                updated_by=updated_by,
            )
            self.active_version_id = version_id
            return activation

        async def delete_version(self, version_id):
            """保持真实 repository 禁止删除 active 版本的约束。"""
            await super().delete_version(version_id)
            if version_id == self.active_version_id:
                raise ValueError("active 文档版本不能通过 delete_version 删除")

    class QueryableCollection(_FakeCollection):
        """记录可按版本读取和删除的 Milvus 行。"""

        def __init__(self):
            """初始化空投影。"""
            super().__init__()
            self.rows: list[dict[str, str]] = []

        def query(self, **_kwargs):
            """返回当前仍存在的投影行。"""
            return list(self.rows)

        def delete(self, expression):
            """模拟按版本删除投影。"""
            super().delete(expression)
            if 'version_id == "version-new"' in expression:
                self.rows.clear()

    kb = MilvusKB.__new__(MilvusKB)
    repository = StatefulRepository()
    collection = QueryableCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)

    async def insert_projection(*_args, **_kwargs):
        """写入待激活的新版本投影。"""
        collection.rows = [{"child_id": "child-1", "version_id": "version-new"}]

    async def cancel_after_activation(_kb_id):
        """在激活提交后的第一个 await 注入取消。"""
        if cancellation_point == "after_activation":
            raise asyncio.CancelledError("cancelled after activation")

    async def embed(_texts):
        """返回确定性测试向量。"""
        return [[0.1, 0.2, 0.3, 0.4]]

    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", insert_projection)
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", cancel_after_activation)

    indexing = kb._index_parent_child_file(
        kb_id="kb-1",
        file_id="file-1",
        file_meta={"markdown_file": "unused", "filename": "a.md"},
        params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
        embedding_model_spec="provider:model",
        embedding_function=embed,
        processing_task_id="task-1",
        processing_owner="worker-1",
        markdown_content="markdown",
    )
    if cancellation_point == "during_activation":
        indexing_task = asyncio.create_task(indexing)
        await activation_entered.wait()
        indexing_task.cancel()
        release_activation.set()
        with pytest.raises(milvus_module._CommittedParentChildIndex) as exc_info:
            await indexing_task
        assert isinstance(exc_info.value.cause, asyncio.CancelledError)
        assert "after Parent-Child activation" in str(exc_info.value.cause)
    else:
        with pytest.raises(milvus_module._CommittedParentChildIndex) as exc_info:
            await indexing
        assert isinstance(exc_info.value.cause, asyncio.CancelledError)
        assert "after activation" in str(exc_info.value.cause)

    assert repository.active_version_id == "version-new"
    assert ("delete", "version-new") not in repository.calls
    assert collection.query() == [{"child_id": "child-1", "version_id": "version-new"}]
    assert not any("version-new" in expression for expression in collection.deleted)


@pytest.mark.asyncio
async def test_parent_child_activation_invalidates_old_parent_when_projection_cleanup_fails(monkeypatch):
    """旧 Milvus 投影清理失败时必须创建可观察且可重建的 Durable Task。"""

    class CleanupFailingCollection(_FakeCollection):
        def delete(self, expression):
            """记录删除并模拟旧维度集合不可用。"""
            super().delete(expression)
            if "version-old" in expression:
                raise RuntimeError("cleanup unavailable")

    class SuccessfulCollection(_FakeCollection):
        def query(self, **_kwargs):
            """返回新版本写入后的回读行。"""
            return [{"child_id": "child-1"}]

    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    new_collection = SuccessfulCollection()
    old_collection = CleanupFailingCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, new_collection)
    monkeypatch.setattr(
        kb,
        "_get_existing_child_collection",
        lambda dimension: old_collection if dimension == 1024 else None,
    )
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))
    invalidated_versions = []
    monkeypatch.setattr(
        milvus_module,
        "invalidate_parent_version",
        lambda kb_id, version_id: _record_async(invalidated_versions, (kb_id, version_id)),
    )
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", lambda *_args: _async_value(None))
    enqueued_tasks = []

    class FakeTasker:
        """记录 superseded 清理的持久任务提交。"""

        async def enqueue_unique_by_payload(self, **kwargs):
            """返回新建的确定性任务快照。"""
            enqueued_tasks.append(kwargs)
            return SimpleNamespace(id="cleanup-task-1"), True

    monkeypatch.setattr("yuxi.services.task_service.tasker", FakeTasker())

    class FakeGraphService:
        async def delete_parent_child_version_graph(self, kb_id, file_id, version_id):
            """模拟旧版本图谱投影已完成清理。"""
            del kb_id, file_id, version_id

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    async def embed(_texts):
        return [[0.1, 0.2, 0.3, 0.4]]

    result = await kb._index_parent_child_file(
        kb_id="kb-1",
        file_id="file-1",
        file_meta={"markdown_file": "unused", "filename": "a.md"},
        params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
        embedding_model_spec="provider:model",
        embedding_function=embed,
        processing_task_id="task-1",
        processing_owner="worker-1",
        markdown_content="markdown",
    )

    assert result["status"] == "indexed"
    assert invalidated_versions == [("kb-1", "version-old")]
    assert any("version-old" in expression for expression in old_collection.deleted)
    assert not any("version-old" in expression for expression in new_collection.deleted)
    assert ("delete", "version-old") not in repository.calls
    assert enqueued_tasks == [
        {
            "name": "Parent-Child 旧版本清理 (file-1)",
            "task_type": "knowledge_parent_child_cleanup",
            "payload": {"kb_id": "kb-1", "file_id": "file-1", "version_id": "version-old"},
            "payload_match": {"version_id": "version-old"},
        }
    ]


@pytest.mark.asyncio
async def test_parent_child_activation_enqueues_cleanup_before_cache_invalidation_failure(monkeypatch):
    """缓存失效异常不能跳过旧版本清理的 Durable Task 回退。"""
    kb = MilvusKB.__new__(MilvusKB)
    cleanup_calls = []
    enqueued_tasks = []

    async def fail_cleanup(kb_id, file_id, version_id, *, control_check=None):
        """模拟已提交后旧投影清理失败。"""
        assert control_check is None
        cleanup_calls.append((kb_id, file_id, version_id))
        raise RuntimeError("cleanup unavailable")

    async def enqueue_cleanup(kb_id, file_id, version_id):
        """记录回退任务，证明其早于缓存失效提交。"""
        enqueued_tasks.append((kb_id, file_id, version_id))
        return "cleanup-task-1"

    async def fail_query_cache(_kb_id):
        """模拟缓存层出现未捕获错误。"""
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(kb, "cleanup_superseded_parent_child_version", fail_cleanup)
    monkeypatch.setattr(kb, "_enqueue_superseded_parent_child_cleanup", enqueue_cleanup)
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", fail_query_cache)
    monkeypatch.setattr(milvus_module, "invalidate_parent_version", lambda *_args: _async_value(None))

    with pytest.raises(RuntimeError, match="cache unavailable"):
        await kb._finish_parent_child_activation(
            "kb-1",
            "file-1",
            SimpleNamespace(version_id="version-old"),
        )

    assert cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert enqueued_tasks == [("kb-1", "file-1", "version-old")]


@pytest.mark.asyncio
async def test_parent_child_activation_stops_cleanup_when_task_lease_is_lost_after_graph(monkeypatch):
    """旧 owner 在图谱删除后丢失 lease 时不得继续删除 successor 的其余投影。"""

    class SuccessfulCollection(_FakeCollection):
        """返回新版本的可回读 child 行。"""

        def query(self, **_kwargs):
            """让流程进入版本激活与旧版本清理阶段。"""
            return [{"child_id": "child-1"}]

    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    new_collection = SuccessfulCollection()
    old_collection = _FakeCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, new_collection)
    monkeypatch.setattr(kb, "_get_existing_child_collection", lambda _dimension: old_collection)
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(milvus_module, "invalidate_parent_version", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", lambda *_args: _async_value(None))

    graph_cleanup_calls = []

    class FakeGraphService:
        """记录已完成的图谱删除。"""

        async def delete_parent_child_version_graph(self, kb_id, file_id, version_id):
            """完成第一段外部投影清理。"""
            graph_cleanup_calls.append((kb_id, file_id, version_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    control_checks = []

    class TaskRepositoryWithLostLease:
        """在图谱删除后的 fence 拒绝旧 attempt。"""

        async def check_control(self, task_id, *, worker_id):
            """第二次检查时报告 lease 已失效。"""
            control_checks.append((task_id, worker_id))
            if len(control_checks) == 2:
                return False, False
            return True, False

    monkeypatch.setattr(milvus_module, "TaskRepository", TaskRepositoryWithLostLease)

    enqueued_tasks = []

    class FakeTasker:
        """记录租约丢失后创建的去重清理意图。"""

        async def enqueue_unique_by_payload(self, **kwargs):
            """返回已创建的清理任务。"""
            enqueued_tasks.append(kwargs)
            return SimpleNamespace(id="cleanup-task-1"), True

    monkeypatch.setattr("yuxi.services.task_service.tasker", FakeTasker())

    result = await kb._index_parent_child_file(
        kb_id="kb-1",
        file_id="file-1",
        file_meta={"markdown_file": "unused", "filename": "a.md"},
        params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
        embedding_model_spec="provider:model",
        embedding_function=lambda _texts: _async_value([[0.1, 0.2, 0.3, 0.4]]),
        processing_task_id="task-old",
        processing_owner="worker-old",
        markdown_content="markdown",
    )

    assert result["status"] == "indexed"
    assert graph_cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert control_checks == [("task-old", "worker-old"), ("task-old", "worker-old")]
    assert old_collection.deleted == []
    assert ("delete", "version-old") not in repository.calls
    assert enqueued_tasks == [
        {
            "name": "Parent-Child 旧版本清理 (file-1)",
            "task_type": "knowledge_parent_child_cleanup",
            "payload": {"kb_id": "kb-1", "file_id": "file-1", "version_id": "version-old"},
            "payload_match": {"version_id": "version-old"},
        }
    ]


@pytest.mark.asyncio
async def test_parent_child_activation_cleans_superseded_storage_and_uses_parent_tokens(monkeypatch):
    """激活成功后清理旧版本全存储，并以父块统计文件 token。"""
    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()

    class SuccessfulCollection(_FakeCollection):
        def query(self, **_kwargs):
            return [{"child_id": "child-1"}]

    new_collection = SuccessfulCollection()
    old_collection = _FakeCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, new_collection)
    dimensions = []

    def get_old_collection(dimension):
        """记录旧版本维度并返回对应集合。"""
        dimensions.append(dimension)
        return old_collection

    monkeypatch.setattr(kb, "_get_existing_child_collection", get_old_collection)
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(milvus_module, "invalidate_parent_version", lambda *_args: _async_value(None))
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", lambda *_args: _async_value(None))
    graph_cleanup_calls = []

    class FakeGraphService:
        async def delete_parent_child_version_graph(self, kb_id, file_id, version_id):
            graph_cleanup_calls.append((kb_id, file_id, version_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    async def embed(_texts):
        return [[0.1, 0.2, 0.3, 0.4]]

    result = await kb._index_parent_child_file(
        kb_id="kb-1",
        file_id="file-1",
        file_meta={"markdown_file": "unused", "filename": "a.md"},
        params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
        embedding_model_spec="provider:model",
        embedding_function=embed,
        processing_task_id="task-1",
        processing_owner="worker-1",
        markdown_content="markdown",
    )

    assert graph_cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert dimensions == [1024]
    assert ("delete", "version-old") in repository.calls
    assert any("version-old" in expression for expression in old_collection.deleted)
    assert not any("version-old" in expression for expression in new_collection.deleted)
    assert result["chunk_count"] == 1
    assert result["token_count"] == 7


async def _async_value(value):
    """返回异步测试替身结果。"""
    return value


async def _record_async(calls, value):
    """记录异步调用参数并返回空结果。"""
    calls.append(value)
