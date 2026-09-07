"""Milvus Parent-Child 入库流程的失败回滚测试。"""

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

    async def get_active_version(self, _kb_id, _file_id):
        return SimpleNamespace(version_id="version-old")

    async def create_staging_version(self, **_kwargs):
        self.calls.append(("create", "version-new"))
        return _FakeVersion()

    async def batch_insert_parent_chunks(self, version_id, _parents):
        self.calls.append(("parents", version_id))

    async def batch_insert_child_chunks(self, version_id, _children):
        self.calls.append(("children", version_id))

    async def activate_version(self, version_id):
        self.calls.append(("activate", version_id))

    async def delete_version(self, version_id):
        self.calls.append(("delete", version_id))


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
    monkeypatch.setattr(kb, "_read_markdown_from_minio", lambda *_args: _async_value("markdown"))


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
        )

    assert repository.calls == [("create", "version-new"), ("delete", "version-new")]


@pytest.mark.asyncio
async def test_parent_child_activation_invalidates_old_parent_when_projection_cleanup_fails(monkeypatch):
    """旧 Milvus 投影清理失败时仍必须失效已 supersede 的父块缓存。"""

    class CleanupFailingCollection(_FakeCollection):
        def query(self, **_kwargs):
            return [{"child_id": "child-1"}]

        def delete(self, expression):
            super().delete(expression)
            if "version-old" in expression:
                raise RuntimeError("cleanup unavailable")

    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()
    collection = CleanupFailingCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)
    monkeypatch.setattr(kb, "_insert_child_chunks_to_milvus", lambda *_args, **_kwargs: _async_value(None))
    invalidated_versions = []
    monkeypatch.setattr(
        milvus_module,
        "invalidate_parent_version",
        lambda kb_id, version_id: _record_async(invalidated_versions, (kb_id, version_id)),
    )
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", lambda *_args: _async_value(None))

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
        markdown_content="markdown",
    )

    assert result["status"] == "indexed"
    assert invalidated_versions == [("kb-1", "version-old")]


@pytest.mark.asyncio
async def test_parent_child_activation_cleans_superseded_storage_and_uses_parent_tokens(monkeypatch):
    """激活成功后清理旧版本全存储，并以父块统计文件 token。"""
    kb = MilvusKB.__new__(MilvusKB)
    repository = _FakeParentChildRepository()

    class SuccessfulCollection(_FakeCollection):
        def query(self, **_kwargs):
            return [{"child_id": "child-1"}]

    collection = SuccessfulCollection()
    _configure_parent_child_flow(monkeypatch, kb, repository, collection)
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
        markdown_content="markdown",
    )

    assert graph_cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert ("delete", "version-old") in repository.calls
    assert any("version-old" in expression for expression in collection.deleted)
    assert result["chunk_count"] == 1
    assert result["token_count"] == 7


async def _async_value(value):
    """返回异步测试替身结果。"""
    return value


async def _record_async(calls, value):
    """记录异步调用参数并返回空结果。"""
    calls.append(value)
