from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from pymilvus import utility

import yuxi.knowledge.implementations.milvus as milvus_module
from yuxi.knowledge.implementations.milvus import CHILD_SPARSE_FIELD, MilvusKB


@pytest.mark.integration
def test_single_chunk_collection_round_trips_model_sparse_and_disabled_empty_mapping(monkeypatch):
    """真实 Milvus 回读单层模型 sparse 权重与关闭状态的空映射。"""

    class FakeChunkRepo:
        """隔离本测试不关注的 PostgreSQL 双写。"""

        async def batch_upsert(self, chunks):
            """接受测试 chunk 投影。"""
            return chunks

        async def delete_by_file_id(self, file_id):
            """提供异常补偿接口。"""
            del file_id

    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepo)
    try:
        kb = MilvusKB(
            f"/tmp/milvus-single-sparse-test-{uuid.uuid4().hex}",
            milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus is unavailable: {exc}")

    collection = None
    try:
        collection_name = f"kb_single_sparse_{uuid.uuid4().hex}"
        collection = kb._create_new_collection(
            collection_name,
            SimpleNamespace(dimension=4, model_id="BAAI/bge-m3"),
            collection_name,
        )
        assert CHILD_SPARSE_FIELD in {field.name for field in collection.schema.fields}

        sparse_chunk = {
            "id": f"row_{uuid.uuid4().hex}",
            "content": "single sparse enabled",
            "chunk_id": "chunk_sparse_enabled",
            "file_id": "file_sparse_enabled",
            "chunk_index": 0,
        }
        dense_chunk = {
            "id": f"row_{uuid.uuid4().hex}",
            "content": "single sparse disabled",
            "chunk_id": "chunk_sparse_disabled",
            "file_id": "file_sparse_disabled",
            "chunk_index": 1,
        }
        asyncio.run(
            kb._insert_chunks_to_stores(
                collection_name,
                "file_sparse_enabled",
                collection,
                [sparse_chunk],
                [[0.1, 0.2, 0.3, 0.4]],
                sparse_embeddings=[{2: 0.75}],
            )
        )
        asyncio.run(
            kb._insert_chunks_to_stores(
                collection_name,
                "file_sparse_disabled",
                collection,
                [dense_chunk],
                [[0.4, 0.3, 0.2, 0.1]],
            )
        )
        collection.flush()
        collection.load()

        rows = collection.query(
            expr='chunk_id in ["chunk_sparse_enabled", "chunk_sparse_disabled"]',
            output_fields=["chunk_id", CHILD_SPARSE_FIELD],
            limit=2,
        )
        sparse_by_chunk = {row["chunk_id"]: row[CHILD_SPARSE_FIELD] for row in rows}
        assert sparse_by_chunk["chunk_sparse_enabled"][2] == pytest.approx(0.75)
        assert sparse_by_chunk["chunk_sparse_disabled"] == {}
    finally:
        if collection is not None and utility.has_collection(collection.name, using=kb.connection_alias):
            utility.drop_collection(collection.name, using=kb.connection_alias)
        kb.__del__()
