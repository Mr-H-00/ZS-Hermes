import asyncio
import threading
import types
from contextlib import asynccontextmanager

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

import yuxi.knowledge.implementations.milvus as milvus_module
from yuxi.knowledge.base import FileStatus, KnowledgeBase
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.implementations.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    MilvusKB,
)
from yuxi.knowledge.read_models import KnowledgeBaseConfig

EMBEDDING_MODEL_SPEC = "test-provider:test-embedding"


def make_query_config() -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        kb_id="db",
        kb_type="milvus",
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
    )


def test_milvus_kb_initializes_database_with_connection_alias(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    kb.milvus_uri = "http://milvus:19530"
    kb.milvus_token = ""
    kb.milvus_db = "yuxi"
    calls = []

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.connections.connect",
        lambda **kwargs: calls.append(("connect", kwargs)),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.db.list_database",
        lambda **kwargs: calls.append(("list_database", kwargs)) or [],
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.db.create_database",
        lambda name, **kwargs: calls.append(("create_database", name, kwargs)),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.db.using_database",
        lambda name, **kwargs: calls.append(("using_database", name, kwargs)),
    )

    kb._init_connection()

    assert calls == [
        ("connect", {"alias": "test-alias", "uri": "http://milvus:19530", "token": ""}),
        ("list_database", {"using": "test-alias"}),
        ("create_database", "yuxi", {"using": "test-alias"}),
        ("using_database", "yuxi", {"using": "test-alias"}),
    ]


class FakeHit:
    def __init__(self, content: str, distance: float):
        self.distance = distance
        self.entity = {
            "content": content,
            "chunk_id": "chunk-1",
            "file_id": "file-1",
            "chunk_index": 0,
        }


class FakeCollection:
    def __init__(self, distance: float = 0.8):
        self.search_calls = []
        self.hybrid_calls = []
        self.insert_calls = []
        self.distance = distance

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[FakeHit("BM25 result", self.distance)]]

    def hybrid_search(self, **kwargs):
        self.hybrid_calls.append(kwargs)
        return [[FakeHit("Hybrid result", self.distance)]]

    def insert(self, entities):
        self.insert_calls.append(entities)


def make_kb(collection: FakeCollection) -> MilvusKB:
    kb = MilvusKB.__new__(MilvusKB)
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]

    async def get_collection(kb_id: str, embedding_model_spec: str | None):
        del kb_id, embedding_model_spec
        return collection

    async def hydrate_chunk_sources(kb_id: str, chunks: list[dict]) -> None:
        for chunk in chunks:
            chunk["metadata"]["source"] = "demo.md"

    kb._get_or_create_milvus_collection = get_collection
    kb._hydrate_chunk_sources = hydrate_chunk_sources
    return kb


def make_file_record(**overrides):
    """构造可按字段覆盖的知识文件测试记录。"""
    data = {
        "file_id": "file-1",
        "kb_id": "db",
        "parent_id": None,
        "filename": "demo.md",
        "file_type": "md",
        "path": "/tmp/demo.md",
        "minio_url": None,
        "markdown_file": "minio://parsed/db/file-1.md",
        "status": FileStatus.PARSED,
        "content_hash": None,
        "file_size": 0,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": "file",
        "processing_params": {},
        "processing_task_id": None,
        "processing_owner": None,
        "is_folder": False,
        "error_message": None,
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
        "original_filename": None,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


class FakeKnowledgeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []
        self.conditional_update_calls = []
        self.deleted = []
        self.processing_locks = []

    @asynccontextmanager
    async def lock_file_processing(self, kb_id: str, file_id: str):
        """记录文件处理副作用锁的持有区间。"""
        self.processing_locks.append(("enter", kb_id, file_id))
        try:
            yield
        finally:
            self.processing_locks.append(("exit", kb_id, file_id))

    async def get_by_file_id(self, file_id: str):
        return self.records.get(file_id)

    async def update_fields_if_status(
        self,
        *,
        kb_id: str,
        file_id: str,
        allowed_statuses: set[str],
        data: dict,
        processing_task_id: str | None = None,
        processing_owner: str | None = None,
    ):
        """按状态与可选任务 owner 条件更新伪文件记录。"""
        record = self.records.get(file_id)
        self.conditional_update_calls.append(
            (kb_id, file_id, set(allowed_statuses), dict(data), processing_task_id, processing_owner)
        )
        if record is None or record.kb_id != kb_id or record.status not in allowed_statuses:
            return None
        if processing_task_id is not None and record.processing_task_id != processing_task_id:
            return None
        if processing_owner is not None and record.processing_owner != processing_owner:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        return record

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        await asyncio.sleep(0)
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def get_filenames_by_file_ids(self, *, kb_id: str, file_ids: list[str]):
        return {
            file_id: record.filename
            for file_id in file_ids
            if (record := self.records.get(file_id)) is not None and record.kb_id == kb_id
        }

    async def list_file_ids_by_filename_contains(self, *, kb_id: str, filename_pattern: str, limit: int = 10_000):
        return [
            file_id
            for file_id, record in self.records.items()
            if record.kb_id == kb_id and filename_pattern.lower() in record.filename.lower()
        ][:limit]

    async def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.records.pop(file_id, None)


def patch_file_repository(monkeypatch, file_repo: FakeKnowledgeFileRepository) -> None:
    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: file_repo)
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeFileRepository", lambda: file_repo)


async def _record_async(calls, value):
    """记录异步调用参数并原样返回传入值，供测试桩模拟异步读取。"""
    calls.append(value)
    return value


def make_chunk(index: int, content: str = "content") -> dict:
    return {
        "id": f"id-{index}",
        "chunk_id": f"chunk-{index}",
        "file_id": "file-1",
        "chunk_index": index,
        "content": content,
    }


async def test_cleanup_database_resources_offloads_milvus_cleanup(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    event_loop_thread = threading.get_ident()
    cleanup_threads = []
    calls = []

    def record_cleanup(name):
        cleanup_threads.append(threading.get_ident())
        calls.append(name)

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.utility.has_collection",
        lambda kb_id, using: record_cleanup("has_collection") or True,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.utility.drop_collection",
        lambda kb_id, using: record_cleanup("drop_collection"),
    )

    class FakeGraphService:
        def delete_graph(self, kb_id):
            assert kb_id == "db"
            record_cleanup("delete_graph")

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    async def delete_base(self, kb_id):
        calls.append("delete_base")
        return {"message": "删除成功"}

    monkeypatch.setattr(KnowledgeBase, "cleanup_database_resources", delete_base)
    monkeypatch.setattr(
        milvus_module,
        "invalidate_parent_cache",
        lambda kb_id: _record_async(calls, f"invalidate_parent:{kb_id}"),
    )
    monkeypatch.setattr(
        milvus_module,
        "invalidate_query_cache",
        lambda kb_id: _record_async(calls, f"invalidate_query:{kb_id}"),
    )
    kb._delete_kb_child_chunks_from_all_collections = lambda kb_id: _record_async(calls, f"delete_children:{kb_id}")

    result = await kb.cleanup_database_resources("db")

    assert result == {"message": "删除成功"}
    assert calls == [
        "has_collection",
        "drop_collection",
        "delete_graph",
        "delete_children:db",
        "delete_base",
        "invalidate_parent:db",
        "invalidate_query:db",
    ]
    assert cleanup_threads
    assert all(thread_id != event_loop_thread for thread_id in cleanup_threads)


async def test_cleanup_database_resources_preserves_postgres_owner_when_collection_drop_fails(monkeypatch):
    """知识库 Milvus collection 删除失败时不得继续删除 PostgreSQL Owner。"""
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    calls = []
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda name, using: True)

    def fail_drop(name, using):
        assert (name, using) == ("db", "test-alias")
        raise RuntimeError("drop unavailable")

    monkeypatch.setattr(milvus_module.utility, "drop_collection", fail_drop)

    class FakeGraphService:
        def delete_graph(self, kb_id):
            calls.append(("graph", kb_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    async def delete_base(self, kb_id):
        calls.append(("postgres", kb_id))

    monkeypatch.setattr(KnowledgeBase, "cleanup_database_resources", delete_base)

    with pytest.raises(RuntimeError, match="drop unavailable"):
        await kb.cleanup_database_resources("db")

    assert calls == []


async def test_detect_data_inconsistencies_stays_in_milvus_executor(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"

    class FakeCollection:
        def __init__(self, name: str, using: str):
            assert using == "test-alias"
            self.num_entities = 4 if name == "kb_managed" else 2
            self.description = f"collection:{name}"

    class FakeRepository:
        async def get_kb_file_stats(self, kb_id: str):
            assert kb_id == "kb_managed"
            return {"file_count": 0}

    monkeypatch.setattr(milvus_module.utility, "list_collections", lambda using: ["kb_managed", "kb_orphan"])
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda name, using: name == "kb_managed")
    monkeypatch.setattr(milvus_module, "Collection", FakeCollection)
    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeRepository)

    result = await kb.detect_data_inconsistencies({"kb_managed"}, {"kb_managed"})

    assert [item["collection_name"] for item in result["missing_collections"]] == ["kb_orphan"]
    assert result["missing_files"] == [
        {
            "kb_id": "kb_managed",
            "vector_count": 4,
            "metadata_files_count": 0,
            "detected_at": result["missing_files"][0]["detected_at"],
        }
    ]


def test_build_chunk_pg_records_preserves_extraction_result():
    kb = MilvusKB.__new__(MilvusKB)

    records = kb._build_chunk_pg_records(
        "db",
        [
            {
                "chunk_id": "chunk-1",
                "file_id": "file-1",
                "chunk_index": 0,
                "content": "content",
                "extraction_result": {"entities": ["alpha"]},
            }
        ],
    )

    assert records[0]["extraction_result"] == {"entities": ["alpha"]}


async def test_embed_and_store_chunks_batches_embedding_and_insert():
    kb = MilvusKB.__new__(MilvusKB)
    chunks = [make_chunk(index, content=f"text-{index}") for index in range(450)]
    embedding_calls = []
    store_calls = []

    async def embedding_function(texts):
        embedding_calls.append(list(texts))
        return [[float(len(text))] for text in texts]

    async def insert_chunks_to_stores(kb_id, file_id, collection, batch_chunks, embeddings, **kwargs):
        store_calls.append(
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "chunks": list(batch_chunks),
                "embeddings": list(embeddings),
                "kwargs": kwargs,
            }
        )

    kb._insert_chunks_to_stores = insert_chunks_to_stores

    await kb._embed_and_store_chunks(
        "db",
        "file-1",
        FakeCollection(),
        chunks,
        embedding_function,
        chunk_batch_size=200,
    )

    assert [len(call) for call in embedding_calls] == [200, 200, 50]
    assert [len(call["chunks"]) for call in store_calls] == [200, 200, 50]
    assert store_calls[0]["chunks"][0]["chunk_id"] == "chunk-0"
    assert store_calls[1]["chunks"][0]["chunk_id"] == "chunk-200"
    assert store_calls[2]["chunks"][0]["chunk_id"] == "chunk-400"
    assert all(call["kwargs"] == {} for call in store_calls)


def test_calculate_chunk_stats_counts_chunks_and_tokens():
    kb = MilvusKB.__new__(MilvusKB)
    chunks = [make_chunk(0, content="alpha beta"), make_chunk(1, content="中文")]

    stats = kb._calculate_chunk_stats(chunks)

    assert stats == {
        "chunk_count": 2,
        "token_count": count_tokens("alpha beta") + count_tokens("中文"),
    }


async def test_index_file_persists_chunk_stats(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)
    collection = FakeCollection()
    deleted_files = []
    store_calls = []
    chunks = [make_chunk(0, content="alpha beta"), make_chunk(1, content="中文")]

    async def get_collection(kb_id, embedding_model_spec):
        del kb_id, embedding_model_spec
        return collection

    async def read_markdown(path):
        return "# demo"

    async def embedding_function(texts):
        return [[0.1, 0.2] for _ in texts]

    async def delete_file_chunks_only(kb_id, file_id, **kwargs):
        del kwargs
        deleted_files.append((kb_id, file_id))

    async def embed_and_store_chunks(
        kb_id,
        file_id,
        collection_arg,
        chunk_records,
        embedding_fn,
        *,
        sparse_enabled=False,
    ):
        store_calls.append((kb_id, file_id, collection_arg, list(chunk_records), embedding_fn, sparse_enabled))

    kb._get_or_create_milvus_collection = get_collection
    kb._read_markdown_from_minio = read_markdown
    kb._split_text_into_chunks = lambda text, file_id, filename, params: chunks
    kb._get_embedding_function = lambda embedding_model_spec: embedding_function
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks

    async def get_system_options(_option, _db=None):
        return {"embed_model": EMBEDDING_MODEL_SPEC}

    monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)

    result = await kb.index_file(
        "db",
        "file-1",
        operator_id="user-1",
        params={},
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
        additional_params={},
    )

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert store_calls[0][5] is False
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 2
    assert result["token_count"] == count_tokens("alpha beta") + count_tokens("中文")
    assert file_repo.records["file-1"].chunk_count == result["chunk_count"]
    assert file_repo.conditional_update_calls[0][3]["status"] == FileStatus.INDEXING
    assert file_repo.conditional_update_calls[-1][3]["status"] == FileStatus.INDEXED


async def test_single_to_parent_child_cleans_legacy_projection_and_clears_owner(monkeypatch):
    """single 转 Parent-Child 时先清旧投影，激活事务同时发布文件终态。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-1": make_file_record(
                status=FileStatus.INDEXED,
                chunk_count=2,
                token_count=10,
                processing_params={"indexing_path": "single_chunk"},
            )
        }
    )
    patch_file_repository(monkeypatch, file_repo)
    calls = []

    async def delete_file_chunks_only(kb_id, file_id, **kwargs):
        """记录切换前的旧单层投影清理及其 attempt owner。"""
        calls.append(("cleanup", kb_id, file_id, kwargs))

    async def index_parent_child(**kwargs):
        """模拟激活事务原子发布版本与文件终态。"""
        assert calls == [
            (
                "cleanup",
                "db",
                "file-1",
                {"processing_task_id": "task-1", "processing_owner": "worker-1"},
            )
        ]
        calls.append(("parent_child", kwargs["kb_id"], kwargs["file_id"]))
        record = file_repo.records["file-1"]
        record.status = FileStatus.INDEXED
        record.chunk_count = 3
        record.token_count = 20
        record.processing_task_id = None
        record.processing_owner = None
        return {"chunk_count": 3, "token_count": 20, "status": FileStatus.INDEXED}

    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._index_parent_child_file = index_parent_child
    kb._get_embedding_function = lambda _embedding_model_spec: object()

    result = await kb.index_file(
        "db",
        "file-1",
        params={"parent_child": {"enabled": True}},
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
        additional_params={},
        processing_task_id="task-1",
        processing_owner="worker-1",
    )

    assert calls == [
        (
            "cleanup",
            "db",
            "file-1",
            {"processing_task_id": "task-1", "processing_owner": "worker-1"},
        ),
        ("parent_child", "db", "file-1"),
    ]
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 3
    assert result["token_count"] == 20
    assert file_repo.records["file-1"].processing_task_id is None
    assert file_repo.records["file-1"].processing_owner is None
    assert len(file_repo.conditional_update_calls) == 1
    assert file_repo.conditional_update_calls[0][3]["processing_task_id"] == "task-1"
    assert file_repo.conditional_update_calls[0][3]["processing_owner"] == "worker-1"


async def test_parent_child_requires_durable_task_owner_before_claim(monkeypatch):
    """Parent-Child 缺少 Durable Task owner 时不得认领文件或改写状态。"""
    kb = MilvusKB.__new__(MilvusKB)
    original_params = {"indexing_path": "single_chunk"}
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-1": make_file_record(
                status=FileStatus.INDEXED,
                processing_params=original_params,
                chunk_count=2,
                token_count=10,
            )
        }
    )
    patch_file_repository(monkeypatch, file_repo)

    with pytest.raises(ValueError, match="Durable Task owner"):
        await kb.index_file(
            "db",
            "file-1",
            params={"parent_child": {"enabled": True}},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
        )

    record = file_repo.records["file-1"]
    assert record.status == FileStatus.INDEXED
    assert record.processing_params == original_params
    assert record.chunk_count == 2
    assert record.token_count == 10
    assert file_repo.conditional_update_calls == []


async def test_parent_child_committed_cancellation_publishes_indexed_before_propagating(monkeypatch):
    """原子激活已提交后的取消不能回写 error_indexing。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-1": make_file_record(
                processing_params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
            )
        }
    )
    patch_file_repository(monkeypatch, file_repo)

    async def committed_then_cancelled(**_kwargs):
        """模拟内部 active 提交已完成后收到取消。"""
        record = file_repo.records["file-1"]
        record.status = FileStatus.INDEXED
        record.error_message = None
        record.chunk_count = 3
        record.token_count = 20
        record.processing_task_id = None
        record.processing_owner = None
        cancellation = asyncio.CancelledError("cancelled after activation")
        raise milvus_module._CommittedParentChildIndex(
            {"chunk_count": 3, "token_count": 20, "status": FileStatus.INDEXED},
            cancellation,
        ) from cancellation

    kb._index_parent_child_file = committed_then_cancelled
    kb._get_embedding_function = lambda _embedding_model_spec: object()

    with pytest.raises(asyncio.CancelledError, match="after activation"):
        await kb.index_file(
            "db",
            "file-1",
            params={"parent_child": {"enabled": True}},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    record = file_repo.records["file-1"]
    assert record.status == FileStatus.INDEXED
    assert record.error_message is None
    assert record.chunk_count == 3
    assert record.token_count == 20
    terminal_statuses = [call[3]["status"] for call in file_repo.conditional_update_calls[1:]]
    assert terminal_statuses == []


async def test_parent_child_success_does_not_overwrite_new_processing_owner(monkeypatch):
    """旧 attempt 完成时若 owner 已切换，不得发布文件成功终态或统计。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-1": make_file_record(
                processing_params={"indexing_path": "parent_child", "parent_child": {"enabled": True}},
                chunk_count=1,
                token_count=7,
            )
        }
    )
    patch_file_repository(monkeypatch, file_repo)

    async def index_parent_child(**_kwargs):
        """模拟索引期间 Durable Task ownership 已转交给新 worker。"""
        record = file_repo.records["file-1"]
        record.processing_task_id = "task-2"
        record.processing_owner = "worker-2"
        raise asyncio.CancelledError("File processing owner was lost")

    kb._index_parent_child_file = index_parent_child
    kb._get_embedding_function = lambda _embedding_model_spec: object()

    with pytest.raises(asyncio.CancelledError, match="owner was lost"):
        await kb.index_file(
            "db",
            "file-1",
            params={"parent_child": {"enabled": True}},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    record = file_repo.records["file-1"]
    assert record.status == FileStatus.INDEXING
    assert record.chunk_count == 1
    assert record.token_count == 7
    assert record.processing_task_id == "task-2"
    assert record.processing_owner == "worker-2"
    terminal_calls = file_repo.conditional_update_calls[1:]
    assert [call[3]["status"] for call in terminal_calls] == [FileStatus.ERROR_INDEXING]
    assert all(call[4:] == ("task-1", "worker-1") for call in terminal_calls)


@pytest.mark.parametrize("operation", ["parse", "index"])
@pytest.mark.parametrize(
    "owner_kwargs",
    [
        {"processing_task_id": "task-1", "processing_owner": None},
        {"processing_task_id": None, "processing_owner": "worker-1"},
    ],
)
async def test_file_processing_owner_pair_is_rejected_before_claim(monkeypatch, operation, owner_kwargs):
    """文件处理入口收到半套 Task owner 时不得认领或改写文件。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)

    with pytest.raises(ValueError, match="必须同时提供"):
        if operation == "parse":
            await kb.parse_file(
                "db",
                "file-1",
                additional_params={},
                embedding_model_spec=EMBEDDING_MODEL_SPEC,
                **owner_kwargs,
            )
        else:
            await kb.index_file(
                "db",
                "file-1",
                embedding_model_spec=EMBEDDING_MODEL_SPEC,
                additional_params={},
                **owner_kwargs,
            )

    assert file_repo.conditional_update_calls == []


async def test_parse_file_persists_request_params_with_owner_claim(monkeypatch):
    """解析请求参数必须与 parsing 状态及 Task owner 在同一次认领中写入。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(status=FileStatus.UPLOADED, path=None, processing_params={"kept": True})}
    )
    patch_file_repository(monkeypatch, file_repo)

    with pytest.raises(ValueError, match="no valid path"):
        await kb.parse_file(
            "db",
            "file-1",
            params={"chunk_parser_config": {"chunk_token_num": 256}},
            additional_params={},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            processing_task_id="task-1",
            processing_owner="worker-1",
        )

    claim = file_repo.conditional_update_calls[0][3]
    assert claim["status"] == FileStatus.PARSING
    assert claim["processing_task_id"] == "task-1"
    assert claim["processing_owner"] == "worker-1"
    assert claim["processing_params"]["kept"] is True
    assert claim["processing_params"]["chunk_parser_config"]["chunk_token_num"] == 256
    assert file_repo.processing_locks == [("enter", "db", "file-1"), ("exit", "db", "file-1")]


@pytest.mark.parametrize(
    ("operation", "expected_status", "expected_message"),
    [
        ("parse", FileStatus.ERROR_PARSING, "File parsing was cancelled"),
        ("index", FileStatus.ERROR_INDEXING, "File indexing was cancelled"),
    ],
)
async def test_cancellation_marks_file_retryable(monkeypatch, operation, expected_status, expected_message):
    kb = MilvusKB.__new__(MilvusKB)
    started = asyncio.Event()
    release_step = asyncio.Event()
    if operation == "parse":
        file_repo = FakeKnowledgeFileRepository(
            {"file-1": make_file_record(markdown_file=None, status=FileStatus.UPLOADED)}
        )
        patch_file_repository(monkeypatch, file_repo)

        async def cancelled_step(*args, **kwargs):
            started.set()
            await release_step.wait()

        monkeypatch.setattr("yuxi.services.ocr_service.parse_document", cancelled_step)
        task = asyncio.create_task(
            kb.parse_file(
                "db",
                "file-1",
                operator_id="user-1",
                additional_params={},
            )
        )
    else:
        file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
        patch_file_repository(monkeypatch, file_repo)

        async def get_collection(kb_id, embedding_model_spec):
            del kb_id, embedding_model_spec
            return FakeCollection()

        async def cancelled_step(path):
            started.set()
            await release_step.wait()

        kb._get_or_create_milvus_collection = get_collection
        kb._get_embedding_function = lambda embedding_model_spec: None
        kb._read_markdown_from_minio = cancelled_step

        async def get_system_options(_option, _db=None):
            return {"embed_model": EMBEDDING_MODEL_SPEC}

        monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)
        task = asyncio.create_task(
            kb.index_file(
                "db",
                "file-1",
                operator_id="user-1",
                params={},
                embedding_model_spec=EMBEDDING_MODEL_SPEC,
                additional_params={},
            )
        )

    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=0.5)
        assert task in done
        with pytest.raises(asyncio.CancelledError):
            task.result()
    finally:
        release_step.set()
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

    record = file_repo.records["file-1"]
    assert record.status == expected_status
    assert record.error_message == expected_message
    assert file_repo.processing_locks == [("enter", "db", "file-1"), ("exit", "db", "file-1")]


async def test_milvus_delete_finishes_sync_side_effect_before_propagating_cancellation():
    """同步删除仍运行时，取消不能让文件处理协程提前退出。"""
    kb = MilvusKB.__new__(MilvusKB)
    delete_started = threading.Event()
    release_delete = threading.Event()

    class BlockingCollection:
        """提供可控同步删除窗口的最小 Milvus collection。"""

        @staticmethod
        def query(**_kwargs):
            return [{"id": "chunk-1"}]

        @staticmethod
        def delete(_expr):
            delete_started.set()
            release_delete.wait()

    deletion = asyncio.create_task(kb._delete_file_chunks_from_milvus(BlockingCollection(), "file-1"))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(delete_started.wait, 1), timeout=2) is True
        deletion.cancel()
        await asyncio.sleep(0.05)
        assert deletion.done() is False

        release_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(deletion, timeout=1)
    finally:
        release_delete.set()
        deletion.cancel()
        await asyncio.gather(deletion, return_exceptions=True)


async def test_delete_file_chunks_only_resets_file_stats(monkeypatch):
    """外部清理成功后再收敛单层与 Parent-Child 的 PostgreSQL Owner。"""
    repos = []
    active_version_delete_calls = []
    cleanup_order = []

    class FakeChunkRepo:
        def __init__(self):
            self.delete_calls = []
            repos.append(self)

        async def count_graph_indexed_by_file_id(self, file_id):
            return 0

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            cleanup_order.append(("legacy_postgres", file_id))
            return 2

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)

    class FakeParentChildRepo:
        async def delete_active_by_file_id(self, kb_id, file_id):
            """记录 active Parent-Child 版本的事务化删除请求。"""
            active_version_delete_calls.append((kb_id, file_id))
            cleanup_order.append(("parent_child_postgres", kb_id, file_id))
            return 1

    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepo)
    graph_cleanup_calls = []

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            graph_cleanup_calls.append((kb_id, file_id))
            cleanup_order.append(("graph_external", kb_id, file_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)}
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)

    async def get_collection(kb_id):
        del kb_id
        return None

    kb._get_existing_milvus_collection = get_collection
    kb._delete_file_child_chunks_from_all_collections = lambda kb_id, file_id: _record_async(
        cleanup_order,
        ("child_external", kb_id, file_id),
    )

    await kb.delete_file_chunks_only("db", "file-1")

    assert graph_cleanup_calls == [("db", "file-1")]
    assert repos[0].delete_calls == ["file-1"]
    assert active_version_delete_calls == [("db", "file-1")]
    assert cleanup_order == [
        ("graph_external", "db", "file-1"),
        ("child_external", "db", "file-1"),
        ("legacy_postgres", "file-1"),
        ("parent_child_postgres", "db", "file-1"),
    ]
    assert file_repo.records["file-1"].chunk_count == 0
    assert file_repo.records["file-1"].token_count == 0
    assert file_repo.update_calls == [("file-1", "db", {"chunk_count": 0, "token_count": 0})]


async def test_delete_file_chunks_only_stops_when_graph_cleanup_fails(monkeypatch):
    """图谱清理失败时保留 PostgreSQL chunk 与文件统计，避免外部数据失去 Owner。"""
    chunk_delete_calls = []

    class FakeChunkRepo:
        async def delete_by_file_id(self, file_id):
            chunk_delete_calls.append(file_id)

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            del kb_id, file_id
            raise RuntimeError("graph unavailable")

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)}
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)
    kb._get_existing_milvus_collection = lambda _kb_id: _record_async([], None)

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await kb.delete_file_chunks_only("db", "file-1")

    assert chunk_delete_calls == []
    assert file_repo.records["file-1"].chunk_count == 2
    assert file_repo.records["file-1"].token_count == 10
    assert file_repo.update_calls == []


async def test_delete_file_chunks_only_stops_when_legacy_milvus_cleanup_fails(monkeypatch):
    """旧单层 Milvus 删除失败时不得删除 PostgreSQL chunk 或重置统计。"""
    chunk_delete_calls = []

    class FakeChunkRepo:
        async def delete_by_file_id(self, file_id):
            chunk_delete_calls.append(file_id)

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            del kb_id, file_id

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)}
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = object()
    kb._get_existing_milvus_collection = lambda _kb_id: _record_async([], collection)

    async def fail_legacy_cleanup(collection_arg, file_id):
        assert collection_arg is collection
        assert file_id == "file-1"
        raise RuntimeError("legacy milvus unavailable")

    kb._delete_file_chunks_from_milvus = fail_legacy_cleanup
    kb._delete_file_child_chunks_from_all_collections = lambda *_args: pytest.fail(
        "child cleanup must not start after legacy cleanup fails"
    )

    with pytest.raises(RuntimeError, match="legacy milvus unavailable"):
        await kb.delete_file_chunks_only("db", "file-1")

    assert chunk_delete_calls == []
    assert file_repo.records["file-1"].chunk_count == 2
    assert file_repo.update_calls == []


async def test_delete_file_chunks_only_stops_when_child_milvus_cleanup_fails(monkeypatch):
    """共享 child collection 删除失败时保留 PostgreSQL Owner 与文件统计。"""
    chunk_delete_calls = []
    active_version_delete_calls = []

    class FakeChunkRepo:
        async def delete_by_file_id(self, file_id):
            chunk_delete_calls.append(file_id)

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            del kb_id, file_id

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)

    class FakeParentChildRepo:
        async def delete_active_by_file_id(self, kb_id, file_id):
            """记录本不应在外部清理失败后发生的 active 版本删除。"""
            active_version_delete_calls.append((kb_id, file_id))

    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepo)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)}
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)
    kb._get_existing_milvus_collection = lambda _kb_id: _record_async([], None)

    async def fail_child_cleanup(kb_id, file_id):
        assert (kb_id, file_id) == ("db", "file-1")
        raise RuntimeError("child milvus unavailable")

    kb._delete_file_child_chunks_from_all_collections = fail_child_cleanup

    with pytest.raises(RuntimeError, match="child milvus unavailable"):
        await kb.delete_file_chunks_only("db", "file-1")

    assert chunk_delete_calls == []
    assert active_version_delete_calls == []
    assert file_repo.records["file-1"].chunk_count == 2
    assert file_repo.update_calls == []


async def test_delete_file_chunks_only_fences_owner_after_graph_cleanup(monkeypatch):
    """旧 attempt 在图谱清理后失去 owner 时，不得继续删除 successor 投影。"""
    cleanup_calls = []
    successor_projection = {"version": "successor-v1", "exists": True}
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-1": make_file_record(
                status=FileStatus.INDEXING,
                processing_task_id="task-old",
                processing_owner="worker-old",
                chunk_count=2,
                token_count=10,
            )
        }
    )
    patch_file_repository(monkeypatch, file_repo)

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            """模拟旧投影删除后 successor 已接管同一文件。"""
            cleanup_calls.append(("graph", kb_id, file_id))
            record = file_repo.records[file_id]
            record.processing_task_id = "task-successor"
            record.processing_owner = "worker-successor"
            successor_projection.clear()
            successor_projection.update({"version": "successor-v2", "exists": True})

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    class FakeChunkRepo:
        async def delete_by_file_id(self, file_id):
            cleanup_calls.append(("legacy_postgres", file_id))

    class FakeParentChildRepo:
        async def delete_active_by_file_id(self, kb_id, file_id):
            cleanup_calls.append(("parent_child_postgres", kb_id, file_id))

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepo)

    class FakeCollection:
        pass

    async def get_existing_collection(kb_id):
        cleanup_calls.append(("get_collection", kb_id))
        return FakeCollection()

    async def delete_legacy_chunks(collection, file_id):
        del collection
        cleanup_calls.append(("legacy_milvus", file_id))

    async def delete_shared_child_chunks(kb_id, file_id):
        cleanup_calls.append(("shared_child", kb_id, file_id))

    kb = MilvusKB.__new__(MilvusKB)
    kb._get_existing_milvus_collection = get_existing_collection
    kb._delete_file_chunks_from_milvus = delete_legacy_chunks
    kb._delete_file_child_chunks_from_all_collections = delete_shared_child_chunks

    with pytest.raises(asyncio.CancelledError, match="owner was lost"):
        await kb.delete_file_chunks_only(
            "db",
            "file-1",
            processing_task_id="task-old",
            processing_owner="worker-old",
        )

    assert cleanup_calls == [("graph", "db", "file-1")]
    assert successor_projection == {"version": "successor-v2", "exists": True}
    record = file_repo.records["file-1"]
    assert record.processing_task_id == "task-successor"
    assert record.processing_owner == "worker-successor"
    assert record.chunk_count == 2
    assert record.token_count == 10
    assert file_repo.update_calls == []


@pytest.mark.asyncio
async def test_delete_file_chunks_only_cleans_shared_parent_child_collections(monkeypatch):
    """文件删除必须在所有按维度共享的 child collection 中按 kb/file 隔离清理。"""
    delete_calls = []

    class FakeChunkRepo:
        async def count_graph_indexed_by_file_id(self, file_id):
            del file_id
            return 0

        async def delete_by_file_id(self, file_id):
            del file_id
            return 0

    class FakeCollection:
        def __init__(self, name):
            self.name = name

        def delete(self, expression):
            delete_calls.append((self.name, expression))

    class FakeFileRepo:
        async def update_fields(self, **kwargs):
            del kwargs

    class FakeParentChildRepo:
        async def delete_active_by_file_id(self, kb_id, file_id):
            """模拟 active Parent-Child 版本已经随文件作用域完成清理。"""
            assert (kb_id, file_id) == ('kb"quoted', "file-1")
            return 1

    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    kb.child_collections = {768: FakeCollection("rag_child_chunk_768")}
    kb._get_existing_milvus_collection = lambda _kb_id: _record_async([], None)
    monkeypatch.setattr(milvus_module.utility, "list_collections", lambda using: ["rag_child_chunk_768"])
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", lambda: FakeFileRepo())
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", FakeParentChildRepo)
    graph_cleanup_calls = []

    class FakeGraphService:
        async def delete_file_graph(self, kb_id, file_id):
            graph_cleanup_calls.append((kb_id, file_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    await kb.delete_file_chunks_only('kb"quoted', "file-1")

    assert graph_cleanup_calls == [('kb"quoted', "file-1")]
    assert delete_calls == [
        (
            "rag_child_chunk_768",
            'knowledge_base_id == "kb\\"quoted" and file_id == "file-1"',
        )
    ]


async def test_delete_file_invalidates_parent_child_cache_after_metadata_delete(monkeypatch):
    """文件删除提交后必须清理知识库范围内的父块与查询缓存。"""
    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(status=FileStatus.INDEXED)})
    patch_file_repository(monkeypatch, file_repo)
    calls = []
    kb.delete_file_chunks_only = lambda kb_id, file_id: _record_async(calls, ("chunks", kb_id, file_id))
    monkeypatch.setattr(
        milvus_module,
        "invalidate_parent_cache",
        lambda kb_id: _record_async(calls, ("parent_cache", kb_id)),
    )
    monkeypatch.setattr(
        milvus_module,
        "invalidate_query_cache",
        lambda kb_id: _record_async(calls, ("query_cache", kb_id)),
    )

    await kb.delete_file("db", "file-1")

    assert file_repo.deleted == ["file-1"]
    assert calls == [
        ("chunks", "db", "file-1"),
        ("parent_cache", "db"),
        ("query_cache", "db"),
    ]


async def test_collection_lifecycle_calls_are_offloaded_from_event_loop(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.collections = {}
    kb.connection_alias = "test-alias"
    event_loop_thread = threading.get_ident()
    call_threads: list[int] = []
    collection = object()

    def create_collection(_kb_id, _embedding_model_spec):
        call_threads.append(threading.get_ident())
        return collection

    class LoadableCollection:
        def load(self):
            call_threads.append(threading.get_ident())

    def has_collection(*_args, **_kwargs):
        call_threads.append(threading.get_ident())
        return False

    monkeypatch.setattr(kb, "_create_kb_instance_sync", create_collection)
    monkeypatch.setattr(milvus_module.utility, "has_collection", has_collection)

    assert await kb._create_kb_instance("db", EMBEDDING_MODEL_SPEC) is collection
    await kb._initialize_kb_instance(LoadableCollection())
    assert await kb._get_existing_milvus_collection("db") is None

    assert len(call_threads) == 3
    assert all(thread_id != event_loop_thread for thread_id in call_threads)


async def test_milvus_chunk_delete_is_offloaded_from_event_loop():
    kb = MilvusKB.__new__(MilvusKB)
    event_loop_thread = threading.get_ident()
    call_threads: list[int] = []

    class FakeCollection:
        def query(self, **_kwargs):
            call_threads.append(threading.get_ident())
            return [{"id": "chunk-1"}]

        def delete(self, _expr):
            call_threads.append(threading.get_ident())

    await kb._delete_file_chunks_from_milvus(FakeCollection(), "file-1")

    assert len(call_threads) == 2
    assert all(thread_id != event_loop_thread for thread_id in call_threads)


async def test_insert_chunks_to_stores_inserts_current_batch(monkeypatch):
    repos = []

    class FakeChunkRepo:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []
            repos.append(self)

        async def batch_upsert(self, chunks):
            self.upsert_calls.append(chunks)
            return []

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 0

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()
    chunks = [make_chunk(index) for index in range(3)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    assert len(collection.insert_calls) == 1
    assert collection.insert_calls[0][0] == ["id-0", "id-1", "id-2"]
    assert collection.insert_calls[0][5] == embeddings
    assert len(repos[0].upsert_calls) == 1
    assert [record["chunk_id"] for record in repos[0].upsert_calls[0]] == ["chunk-0", "chunk-1", "chunk-2"]


async def test_milvus_insert_finishes_sync_side_effect_before_propagating_cancellation(monkeypatch):
    """同步插入仍运行时，取消不能让文件处理协程提前退出。"""

    class FakeChunkRepo:
        """提供立即完成的 PostgreSQL 双写分支。"""

        @staticmethod
        async def batch_upsert(_chunks):
            return []

    class BlockingCollection(FakeCollection):
        """提供可控同步插入窗口的最小 Milvus collection。"""

        def insert(self, entities):
            del entities
            insert_started.set()
            release_insert.wait()
            insert_finished.set()

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    insert_started = threading.Event()
    release_insert = threading.Event()
    insert_finished = threading.Event()
    chunks = [make_chunk(0)]
    insertion = asyncio.create_task(
        kb._insert_chunks_to_stores("db", "file-1", BlockingCollection(), chunks, [[0.1, 0.2]])
    )

    try:
        assert await asyncio.wait_for(asyncio.to_thread(insert_started.wait, 1), timeout=2) is True
        insertion.cancel()
        await asyncio.sleep(0.05)
        assert insertion.done() is False
        assert insert_finished.is_set() is False

        release_insert.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(insertion, timeout=1)
        assert insert_finished.is_set() is True
    finally:
        release_insert.set()
        insertion.cancel()
        await asyncio.gather(insertion, return_exceptions=True)


async def test_insert_chunks_to_stores_rolls_back_file_when_milvus_insert_fails(monkeypatch):
    repos = []

    class FakeChunkRepo:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []
            repos.append(self)

        async def batch_upsert(self, chunks):
            self.upsert_calls.append(chunks)
            return []

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 0

    class FailingCollection(FakeCollection):
        def insert(self, entities):
            super().insert(entities)
            raise RuntimeError("milvus boom")

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FailingCollection()
    milvus_delete_calls = []

    async def delete_file_chunks_from_milvus(collection_arg, file_id):
        milvus_delete_calls.append((collection_arg, file_id))

    kb._delete_file_chunks_from_milvus = delete_file_chunks_from_milvus
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    with pytest.raises(RuntimeError, match="milvus boom"):
        await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    assert repos[0].delete_calls == ["file-1"]
    assert milvus_delete_calls == [(collection, "file-1")]


async def test_keyword_mode_uses_milvus_bm25_search():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "alpha beta",
        "db",
        config=config,
        search_mode="keyword",
        bm25_top_k=7,
        bm25_drop_ratio_search=0.2,
    )

    assert chunks[0]["content"] == "BM25 result"
    assert chunks[0]["bm25_score"] == 0.8
    search_call = collection.search_calls[0]
    assert search_call["data"] == ["alpha beta"]
    assert search_call["anns_field"] == CONTENT_SPARSE_FIELD
    assert search_call["param"] == {
        "metric_type": "BM25",
        "params": {"drop_ratio_search": 0.2},
    }
    assert search_call["limit"] == 7


async def test_vector_mode_ignores_metric_type_override():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "vector query",
        "db",
        config=config,
        search_mode="vector",
        metric_type="L2",
    )

    assert chunks[0]["content"] == "BM25 result"
    search_call = collection.search_calls[0]
    assert search_call["anns_field"] == "embedding"
    assert search_call["param"]["metric_type"] == VECTOR_METRIC_TYPE


async def test_hybrid_mode_uses_milvus_native_hybrid_search():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        config=config,
        search_mode="hybrid",
        final_top_k=3,
        bm25_top_k=8,
        vector_weight=0.6,
        bm25_weight=0.4,
    )

    assert chunks[0]["content"] == "Hybrid result"
    assert chunks[0]["hybrid_score"] == 0.8
    hybrid_call = collection.hybrid_calls[0]
    assert hybrid_call["limit"] == 3
    assert hybrid_call["rerank"]._weights == [0.6, 0.4]

    vector_request, bm25_request = hybrid_call["reqs"]
    assert vector_request.anns_field == "embedding"
    assert vector_request.data == [[0.1, 0.2]]
    assert vector_request.param["metric_type"] == VECTOR_METRIC_TYPE
    assert bm25_request.anns_field == CONTENT_SPARSE_FIELD
    assert bm25_request.data == ["hybrid query"]
    assert bm25_request.limit == 8
    assert bm25_request.param["metric_type"] == "BM25"


async def test_hybrid_mode_filters_scores_below_similarity_threshold():
    collection = FakeCollection(distance=0.1)
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        config=config,
        search_mode="hybrid",
        final_top_k=3,
        similarity_threshold=0.2,
    )

    assert chunks == []


def test_query_params_config_uses_bm25_parameters():
    kb = MilvusKB.__new__(MilvusKB)

    config = kb.get_query_params_config("db")

    option_keys = {option["key"] for option in config["options"]}
    assert "keyword_top_k" not in option_keys
    assert "metric_type" not in option_keys
    assert {
        "bm25_top_k",
        "vector_weight",
        "bm25_weight",
        "bm25_drop_ratio_search",
    } <= option_keys

    search_mode = next(option for option in config["options"] if option["key"] == "search_mode")
    descriptions = {option["value"]: option["description"] for option in search_mode["options"]}
    assert "BM25" in descriptions["keyword"]
    assert "BM25" in descriptions["hybrid"]


def test_collection_supports_bm25_requires_analyzed_content_sparse_field_and_function():
    kb = MilvusKB.__new__(MilvusKB)
    schema = CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
            ),
            FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
        ],
        functions=[
            Function(
                name="content_bm25",
                input_field_names=["content"],
                output_field_names=[CONTENT_SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        ],
    )

    collection = type("Collection", (), {"schema": schema})()

    assert kb._collection_supports_bm25(collection)
