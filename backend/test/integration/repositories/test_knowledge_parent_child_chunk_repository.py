"""Parent-Child PostgreSQL 持久化集成测试。"""

from __future__ import annotations

from datetime import datetime
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pymilvus import utility
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories import knowledge_chunk_repository as legacy_repo_module
from yuxi.repositories import knowledge_graph_repository as graph_repo_module
from yuxi.repositories import knowledge_parent_child_chunk_repository as parent_child_repo_module
from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import CHILD_KB_FIELD, MilvusKB
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_graph_repository import KnowledgeGraphRepository
from yuxi.repositories.knowledge_parent_child_chunk_repository import KnowledgeParentChildChunkRepository
from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_knowledge import (
    Base,
    KnowledgeBase,
    KnowledgeDocumentVersion,
    KnowledgeFile,
    KnowledgeGraphEntity,
    KnowledgeGraphTriple,
    KnowledgeParentChunk,
    KnowledgeParentGraphEntityMention,
    KnowledgeParentGraphTripleMention,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件使用隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 不创建真实 API 知识库资源。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 不创建 Sandbox 资源。"""
    yield


@pytest_asyncio.fixture()
async def parent_child_database(monkeypatch):
    """创建仅供单个测试使用的 PostgreSQL Schema 与 repository。"""
    schema = f"pytest_parent_child_{uuid.uuid4().hex[:16]}"
    kb_id = f"kb_{uuid.uuid4().hex}"
    file_id = f"file_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = object.__new__(PostgresManager)
        PostgresManager.__init__(manager)
        manager.async_engine = scoped_engine
        manager.AsyncSession = async_sessionmaker(scoped_engine, expire_on_commit=False)
        manager._initialized = True

        async with scoped_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with manager.get_async_session_context() as session:
            session.add(
                KnowledgeBase(
                    kb_id=kb_id,
                    name="Parent-Child integration",
                    kb_type="milvus",
                    embedding_model_spec="provider:BAAI/bge-m3",
                )
            )
            session.add(
                KnowledgeFile(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename="document.md",
                    processing_params={"existing": "kept"},
                    is_folder=False,
                )
            )

        monkeypatch.setattr(parent_child_repo_module, "pg_manager", manager)
        monkeypatch.setattr(legacy_repo_module, "pg_manager", manager)
        monkeypatch.setattr(graph_repo_module, "pg_manager", manager)
        yield SimpleNamespace(
            manager=manager,
            repository=KnowledgeParentChildChunkRepository(),
            engine=scoped_engine,
            kb_id=kb_id,
            file_id=file_id,
        )
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


def _processing_params() -> dict:
    """返回 repository 集成测试使用的已规范化入库参数。"""
    return {
        "chunk_preset_id": "general",
        "indexing_path": "parent_child",
        "parent_child": {
            "enabled": True,
            "parent_token_num": 1000,
            "child_token_num": 200,
        },
    }


def _parent(parent_id: str, parent_index: int = 0) -> dict:
    """构造一个可持久化父块。"""
    return {
        "parent_id": parent_id,
        "parent_index": parent_index,
        "parent_text": "父块正文",
        "start_offset": 0,
        "end_offset": 5,
        "token_count": 3,
        "metadata": {"page_num": 1},
    }


def _child(child_id: str, parent_id: str, child_index: int = 0) -> dict:
    """构造一个带原文 span 的可持久化子块。"""
    return {
        "child_id": child_id,
        "parent_id": parent_id,
        "child_index": child_index,
        "child_text": "子块正文",
        "start_offset": 0,
        "end_offset": 4,
        "token_count": 2,
        "spans": [{"start_offset": 0, "end_offset": 4, "page_num": 1}],
        "metadata": {"source": "document.md"},
    }


def _flow_chunks(_content, file_id, kb_id, version_id, _params, **_kwargs) -> dict:
    """为组合存储测试生成绑定当前版本的确定性父子块。"""
    parent_id = f"parent_{version_id}"
    return {
        "parents": [
            {
                "parent_id": parent_id,
                "parent_index": 0,
                "parent_text": "完整父块正文",
                "start_offset": 0,
                "end_offset": 8,
                "token_count": 4,
                "metadata": {"kb_id": kb_id, "file_id": file_id},
            }
        ],
        "children": [
            {
                "child_id": f"child_{version_id}",
                "parent_id": parent_id,
                "child_index": 0,
                "child_text": "父块正文",
                "start_offset": 2,
                "end_offset": 6,
                "token_count": 2,
                "spans": [{"start_offset": 2, "end_offset": 6, "page_num": 1}],
                "metadata": {"kb_id": kb_id, "file_id": file_id},
            }
        ],
    }


class _ReadBackFailureCollection:
    """代理真实 Milvus collection，并只让激活前 read-back 失败。"""

    def __init__(self, collection):
        self._collection = collection

    def __getattr__(self, name):
        return getattr(self._collection, name)

    def query(self, **_kwargs):
        """返回空结果以触发新版本回滚。"""
        return []


async def _async_value(value):
    """把同步测试替身值包装为异步返回值。"""
    return value


async def _async_none():
    """提供无副作用的异步缓存替身。"""


async def _create_version(database, *, doc_id: str):
    """为测试知识文件创建一个 staging 版本。"""
    return await database.repository.create_staging_version(
        database.kb_id,
        database.file_id,
        doc_id,
        _processing_params(),
        "provider:BAAI/bge-m3",
        1024,
    )


async def test_parent_child_records_round_trip_and_legacy_chunks_remain_readable(parent_child_database) -> None:
    """父子记录可真实回读，且旧 knowledge_chunks 查询不受新表影响。"""
    database = parent_child_database
    version = await _create_version(database, doc_id="doc-round-trip")
    await database.repository.batch_insert_parent_chunks(
        version.version_id,
        [_parent("parent-a", 0), _parent("parent-b", 1)],
    )
    await database.repository.batch_insert_child_chunks(
        version.version_id,
        [_child("child-a", "parent-a", 0), _child("child-b", "parent-b", 1)],
    )

    parents = await database.repository.list_parents_by_ids(["parent-b", "missing", "parent-a"])
    children = await database.repository.list_children_by_ids(["child-b", "missing", "child-a"])
    assert [record.parent_id for record in parents] == ["parent-b", "parent-a"]
    assert [record.child_id for record in children] == ["child-b", "child-a"]
    assert parents[1].chunk_metadata == {"page_num": 1}
    assert children[1].spans == [{"start_offset": 0, "end_offset": 4, "page_num": 1}]

    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": "legacy-chunk",
                "file_id": database.file_id,
                "kb_id": database.kb_id,
                "chunk_index": 0,
                "content": "旧单层块",
            }
        ]
    )
    legacy_chunks = await KnowledgeChunkRepository().list_by_file_id(database.file_id)
    assert [(record.chunk_id, record.content) for record in legacy_chunks] == [("legacy-chunk", "旧单层块")]


async def test_parent_child_file_counts_and_token_totals_follow_active_versions(parent_child_database) -> None:
    """按文件统计必须只看 active ParentChunk，并保留父块顺序。"""
    database = parent_child_database
    version = await _create_version(database, doc_id="doc-file-stats")
    await database.repository.batch_insert_parent_chunks(
        version.version_id,
        [_parent("parent-a", 0), _parent("parent-b", 1)],
    )
    await database.repository.activate_version(version.version_id)

    parents = await database.repository.list_parents_by_file_id(database.file_id)
    parents_by_ids = await database.repository.list_parents_by_file_ids([database.file_id])

    assert [record.parent_id for record in parents] == ["parent-a", "parent-b"]
    assert [record.parent_id for record in parents_by_ids] == ["parent-a", "parent-b"]
    assert await database.repository.count_by_file_ids([database.file_id]) == {database.file_id: 2}
    assert await database.repository.sum_token_count_by_file_ids([database.file_id]) == {database.file_id: 6}


async def test_activation_is_atomic_and_partial_unique_index_rejects_second_active(parent_child_database) -> None:
    """激活切换保持单一 active，非法直接写入不会破坏旧版本。"""
    database = parent_child_database
    first = await _create_version(database, doc_id="doc-first")
    second = await _create_version(database, doc_id="doc-second")
    await database.repository.batch_insert_parent_chunks(first.version_id, [_parent("parent-first")])
    await database.repository.batch_insert_parent_chunks(second.version_id, [_parent("parent-second")])
    await database.repository.activate_version(first.version_id)

    with pytest.raises(IntegrityError):
        async with database.engine.begin() as connection:
            await connection.execute(
                update(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.version_id == second.version_id)
                .values(status="active")
            )

    with pytest.raises(ValueError, match="不存在"):
        await database.repository.activate_version("missing-version")
    active = await database.repository.get_active_version(database.kb_id, database.file_id)
    assert active is not None
    assert active.version_id == first.version_id

    with pytest.raises(ValueError, match="不属于当前文档版本"):
        await database.repository.batch_insert_child_chunks(
            second.version_id,
            [_child("cross-version-child", "parent-first")],
        )

    activated = await database.repository.activate_version(second.version_id)
    assert activated.status == "active"
    async with database.engine.connect() as connection:
        first_status = await connection.scalar(
            select(KnowledgeDocumentVersion.status).where(KnowledgeDocumentVersion.version_id == first.version_id)
        )
        file_params = await connection.scalar(
            select(KnowledgeFile.processing_params).where(KnowledgeFile.file_id == database.file_id)
        )
    assert first_status == "superseded"
    assert file_params["existing"] == "kept"
    assert file_params["document_version_id"] == second.version_id
    assert file_params["indexing_path"] == "parent_child"
    assert file_params["doc_id"] == "doc-second"


async def test_graph_index_counts_exclude_superseded_parent_versions(parent_child_database) -> None:
    """图谱完成统计只能计算 active 版本，不能把暂留旧版本混入。"""
    database = parent_child_database
    first = await _create_version(database, doc_id="doc-first")
    second = await _create_version(database, doc_id="doc-second")
    await database.repository.batch_insert_parent_chunks(first.version_id, [_parent("parent-first")])
    await database.repository.batch_insert_parent_chunks(second.version_id, [_parent("parent-second")])
    await database.repository.activate_version(first.version_id)
    await database.repository.activate_version(second.version_id)

    async with database.manager.get_async_session_context() as session:
        await session.execute(
            update(KnowledgeParentChunk)
            .where(KnowledgeParentChunk.parent_id.in_(["parent-first", "parent-second"]))
            .values(graph_structure_indexed=True, graph_indexed=True)
        )

    assert await database.repository.count_by_kb_id(database.kb_id) == 1
    assert await database.repository.count_graph_structure_indexed_by_kb_id(database.kb_id) == 1
    assert await database.repository.count_graph_indexed_by_kb_id(database.kb_id) == 1


async def test_failed_graph_samples_use_active_parent_chunks_and_recent_order(parent_child_database) -> None:
    """失败样本只读取 active 版本，并按真实 ParentChunk 字段稳定排序。"""
    database = parent_child_database
    superseded = await _create_version(database, doc_id="doc-superseded")
    await database.repository.batch_insert_parent_chunks(
        superseded.version_id,
        [_parent("parent-superseded")],
    )
    await database.repository.activate_version(superseded.version_id)

    active = await _create_version(database, doc_id="doc-active")
    await database.repository.batch_insert_parent_chunks(
        active.version_id,
        [_parent("parent-old", 0), _parent("parent-new", 1)],
    )
    await database.repository.activate_version(active.version_id)

    async with database.manager.get_async_session_context() as session:
        await session.execute(
            update(KnowledgeParentChunk)
            .where(KnowledgeParentChunk.parent_id == "parent-superseded")
            .values(
                graph_extraction_details={"status": "failed", "attempt_count": 1},
                created_at=datetime(2025, 1, 3),
            )
        )
        await session.execute(
            update(KnowledgeParentChunk)
            .where(KnowledgeParentChunk.parent_id == "parent-old")
            .values(
                graph_extraction_details={"status": "failed", "attempt_count": 1},
                created_at=datetime(2025, 1, 1),
            )
        )
        await session.execute(
            update(KnowledgeParentChunk)
            .where(KnowledgeParentChunk.parent_id == "parent-new")
            .values(
                graph_extraction_details={"status": "failed", "attempt_count": 2},
                created_at=datetime(2025, 1, 2),
            )
        )

    samples = await database.repository.list_graph_extraction_failed_samples(database.kb_id)

    assert [sample["chunk_id"] for sample in samples] == ["parent-new", "parent-old"]
    assert samples[0]["details"]["attempt_count"] == 2


async def test_parent_version_graph_reference_cleanup_preserves_shared_entity(parent_child_database) -> None:
    """旧版本图谱引用删除后只回收失去全部引用的实体。"""
    database = parent_child_database
    first = await _create_version(database, doc_id="doc-first")
    second = await _create_version(database, doc_id="doc-second")
    await database.repository.batch_insert_parent_chunks(first.version_id, [_parent("parent-first")])
    await database.repository.batch_insert_parent_chunks(second.version_id, [_parent("parent-second")])
    await database.repository.activate_version(first.version_id)
    await database.repository.activate_version(second.version_id)
    graph_repo = KnowledgeGraphRepository()

    def entity(entity_id: str) -> dict:
        """构造图谱 repository 可持久化的测试实体。"""
        return {
            "entity_id": entity_id,
            "kb_id": database.kb_id,
            "normalized_name": entity_id,
            "label": "Test",
            "name": entity_id,
            "attributes": {},
            "content": entity_id,
        }

    def triple(triple_id: str, source_entity_id: str, target_entity_id: str) -> dict:
        """构造图谱 repository 可持久化的测试三元组。"""
        return {
            "triple_id": triple_id,
            "kb_id": database.kb_id,
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "relation_type": "RELATED_TO",
            "content": triple_id,
            "text": triple_id,
            "extractor_type": "test",
        }

    await graph_repo.upsert_parent_graph(
        kb_id=database.kb_id,
        file_id=database.file_id,
        parent_id="parent-first",
        entities=[entity("entity-old-only"), entity("entity-shared")],
        triples=[triple("triple-old-only", "entity-old-only", "entity-shared")],
    )
    await graph_repo.upsert_parent_graph(
        kb_id=database.kb_id,
        file_id=database.file_id,
        parent_id="parent-second",
        entities=[entity("entity-shared")],
        triples=[],
    )

    file_targets = await graph_repo.list_file_deletion_targets(database.file_id)
    version_targets = await graph_repo.list_parent_version_deletion_targets(first.version_id)

    assert set(file_targets[0]) == {"entity-old-only", "entity-shared"}
    assert file_targets[1] == ["triple-old-only"]
    assert version_targets == (["entity-old-only"], ["triple-old-only"])
    async with database.engine.connect() as connection:
        owner_mentions = set((await connection.execute(select(KnowledgeParentGraphEntityMention.parent_id))).scalars())
        owner_triple_mentions = set(
            (await connection.execute(select(KnowledgeParentGraphTripleMention.parent_id))).scalars()
        )
        owner_entities = set((await connection.execute(select(KnowledgeGraphEntity.entity_id))).scalars())
        owner_triples = set((await connection.execute(select(KnowledgeGraphTriple.triple_id))).scalars())
    assert owner_mentions == {"parent-first", "parent-second"}
    assert owner_triple_mentions == {"parent-first"}
    assert owner_entities == {"entity-old-only", "entity-shared"}
    assert owner_triples == {"triple-old-only"}

    orphan_entities, orphan_triples = await graph_repo.delete_parent_version_references(first.version_id)

    assert orphan_entities == ["entity-old-only"]
    assert orphan_triples == ["triple-old-only"]
    async with database.engine.connect() as connection:
        remaining_mentions = set(
            (await connection.execute(select(KnowledgeParentGraphEntityMention.parent_id))).scalars()
        )
        remaining_triple_mentions = set(
            (await connection.execute(select(KnowledgeParentGraphTripleMention.parent_id))).scalars()
        )
        remaining_entities = set((await connection.execute(select(KnowledgeGraphEntity.entity_id))).scalars())
        remaining_triples = set((await connection.execute(select(KnowledgeGraphTriple.triple_id))).scalars())
    assert remaining_mentions == {"parent-second"}
    assert remaining_triple_mentions == set()
    assert remaining_entities == {"entity-shared"}
    assert remaining_triples == set()


async def test_parent_child_flow_commits_verified_version_and_rolls_back_failed_replacement(
    parent_child_database,
    monkeypatch,
) -> None:
    """真实 PostgreSQL 与 Milvus 共同证明成功激活及失败替换回滚。"""
    database = parent_child_database
    kb = MilvusKB(
        f"/tmp/parent-child-flow-{uuid.uuid4().hex}",
        milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
    )
    dimension = 512 + int(uuid.uuid4().hex[:4], 16) % 20_000
    while utility.has_collection(kb._child_collection_name(dimension), using=kb.connection_alias):
        dimension += 1
    collection_name = kb._child_collection_name(dimension)
    model_info = SimpleNamespace(dimension=dimension, model_type="embedding")
    monkeypatch.setattr(milvus_module, "model_cache", SimpleNamespace(get_model_info=lambda _spec: model_info))
    monkeypatch.setattr(milvus_module, "chunk_markdown_parent_child", _flow_chunks)
    monkeypatch.setattr(milvus_module, "invalidate_query_cache", lambda *_args: _async_none())
    monkeypatch.setattr(milvus_module, "invalidate_parent_version", lambda *_args: _async_none())
    graph_cleanup_calls = []

    class FakeGraphService:
        async def delete_parent_child_version_graph(self, kb_id, file_id, version_id):
            graph_cleanup_calls.append((kb_id, file_id, version_id))

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    async def embed(texts):
        """返回与测试 collection 维度一致的确定性稠密向量。"""
        return [[0.01] * dimension for _ in texts]

    params = _processing_params()
    try:
        first_result = await kb._index_parent_child_file(
            kb_id=database.kb_id,
            file_id=database.file_id,
            file_meta={"markdown_file": "unused", "filename": "document.md"},
            params=params,
            embedding_model_spec="provider:test-embedding",
            embedding_function=embed,
            markdown_content="完整父块正文",
        )
        assert first_result["status"] == "indexed"
        first_active = await database.repository.get_active_version(database.kb_id, database.file_id)
        assert first_active is not None

        collection = kb.child_collections[dimension]
        persisted = collection.query(
            expr=f'{CHILD_KB_FIELD} == "{database.kb_id}"',
            output_fields=["child_id", "version_id"],
        )
        assert [(row["child_id"], row["version_id"]) for row in persisted] == [
            (f"child_{first_active.version_id}", first_active.version_id)
        ]

        second_result = await kb._index_parent_child_file(
            kb_id=database.kb_id,
            file_id=database.file_id,
            file_meta={"markdown_file": "unused", "filename": "document.md"},
            params=params,
            embedding_model_spec="provider:test-embedding",
            embedding_function=embed,
            markdown_content="替换父块正文",
        )
        assert second_result["status"] == "indexed"
        second_active = await database.repository.get_active_version(database.kb_id, database.file_id)
        assert second_active is not None
        assert second_active.version_id != first_active.version_id
        assert graph_cleanup_calls == [(database.kb_id, database.file_id, first_active.version_id)]
        async with database.engine.connect() as connection:
            version_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_document_versions"))
        assert version_count == 1
        collection.flush()
        persisted = collection.query(
            expr=f'{CHILD_KB_FIELD} == "{database.kb_id}"',
            output_fields=["child_id", "version_id"],
        )
        assert [(row["child_id"], row["version_id"]) for row in persisted] == [
            (f"child_{second_active.version_id}", second_active.version_id)
        ]

        proxy = _ReadBackFailureCollection(collection)
        monkeypatch.setattr(kb, "_get_or_create_child_collection", lambda *_args, **_kwargs: _async_value(proxy))
        with pytest.raises(RuntimeError, match="read back"):
            await kb._index_parent_child_file(
                kb_id=database.kb_id,
                file_id=database.file_id,
                file_meta={"markdown_file": "unused", "filename": "document.md"},
                params=params,
                embedding_model_spec="provider:test-embedding",
                embedding_function=embed,
                markdown_content="替换父块正文",
            )

        collection.flush()
        active_after_failure = await database.repository.get_active_version(database.kb_id, database.file_id)
        assert active_after_failure is not None
        assert active_after_failure.version_id == second_active.version_id
        versions = await database.repository.list_active_version_ids(database.kb_id)
        assert versions == [second_active.version_id]
        remaining = collection.query(
            expr=f'{CHILD_KB_FIELD} == "{database.kb_id}"',
            output_fields=["child_id", "version_id"],
        )
        assert [(row["child_id"], row["version_id"]) for row in remaining] == [
            (f"child_{second_active.version_id}", second_active.version_id)
        ]
    finally:
        if utility.has_collection(collection_name, using=kb.connection_alias):
            utility.drop_collection(collection_name, using=kb.connection_alias)
        kb.__del__()


async def test_version_file_and_knowledge_base_deletes_cascade_parent_child_records(parent_child_database) -> None:
    """版本、文件与知识库删除都会级联清理父子块。"""
    database = parent_child_database
    staging = await _create_version(database, doc_id="doc-delete-version")
    await database.repository.batch_insert_parent_chunks(staging.version_id, [_parent("parent-staging")])
    await database.repository.batch_insert_child_chunks(
        staging.version_id,
        [_child("child-staging", "parent-staging")],
    )
    assert await database.repository.delete_version(staging.version_id) is True

    async with database.engine.connect() as connection:
        parent_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_parent_chunks"))
        child_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_child_chunks"))
    assert (parent_count, child_count) == (0, 0)

    active = await _create_version(database, doc_id="doc-delete-file")
    await database.repository.batch_insert_parent_chunks(active.version_id, [_parent("parent-active")])
    await database.repository.batch_insert_child_chunks(
        active.version_id,
        [_child("child-active", "parent-active")],
    )
    await database.repository.activate_version(active.version_id)
    with pytest.raises(ValueError, match="active"):
        await database.repository.delete_version(active.version_id)

    invalid_status = await _create_version(database, doc_id="doc-invalid-status")
    async with database.manager.get_async_session_context() as session:
        await session.execute(
            update(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.version_id == invalid_status.version_id)
            .values(status="failed")
        )
    with pytest.raises(ValueError, match="staging 或 superseded"):
        await database.repository.delete_version(invalid_status.version_id)

    async with database.manager.get_async_session_context() as session:
        await session.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == database.file_id))
    async with database.engine.connect() as connection:
        version_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_document_versions"))
        parent_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_parent_chunks"))
        child_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_child_chunks"))
    assert (version_count, parent_count, child_count) == (0, 0, 0)

    second_file_id = f"file_{uuid.uuid4().hex}"
    async with database.manager.get_async_session_context() as session:
        session.add(
            KnowledgeFile(
                file_id=second_file_id,
                kb_id=database.kb_id,
                filename="second.md",
                processing_params={},
                is_folder=False,
            )
        )
    database.file_id = second_file_id
    kb_version = await _create_version(database, doc_id="doc-delete-kb")
    await database.repository.batch_insert_parent_chunks(kb_version.version_id, [_parent("parent-kb")])
    await database.repository.batch_insert_child_chunks(kb_version.version_id, [_child("child-kb", "parent-kb")])

    async with database.manager.get_async_session_context() as session:
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == database.kb_id))
    async with database.engine.connect() as connection:
        version_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_document_versions"))
        parent_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_parent_chunks"))
        child_count = await connection.scalar(text("SELECT COUNT(*) FROM knowledge_child_chunks"))
    assert (version_count, parent_count, child_count) == (0, 0, 0)


async def test_knowledge_schema_v2_upgrade_is_idempotent_on_legacy_tables() -> None:
    """旧知识库基础表可连续执行两次 v2 收敛，并得到完整父子表与索引。"""
    schema = f"pytest_pc_migration_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = object.__new__(PostgresManager)
        PostgresManager.__init__(manager)
        manager.async_engine = scoped_engine
        manager.AsyncSession = async_sessionmaker(scoped_engine, expire_on_commit=False)
        manager._initialized = True

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE knowledge_bases (
                        id SERIAL PRIMARY KEY,
                        kb_id VARCHAR(80) NOT NULL UNIQUE,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        kb_type VARCHAR(32) NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE knowledge_files (
                        id SERIAL PRIMARY KEY,
                        file_id VARCHAR(64) NOT NULL UNIQUE,
                        kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                        filename VARCHAR(512) NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )

        await manager.ensure_knowledge_schema()
        await manager.ensure_knowledge_schema()

        async with scoped_engine.connect() as connection:
            tables = {
                table_name: await connection.scalar(text("SELECT to_regclass(:name)::text"), {"name": table_name})
                for table_name in (
                    "knowledge_document_versions",
                    "knowledge_parent_chunks",
                    "knowledge_child_chunks",
                    "knowledge_chunks",
                )
            }
            active_index = await connection.scalar(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND indexname = 'uq_knowledge_document_versions_active_file'
                    """
                )
            )
        assert all(tables.values())
        assert "UNIQUE INDEX" in active_index
        assert "WHERE ((status)::text = 'active'::text)" in active_index
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
