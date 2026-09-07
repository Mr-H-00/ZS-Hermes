from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymilvus import utility

from yuxi.knowledge.implementations.milvus import CONTENT_SPARSE_FIELD, MilvusKB


def _live_child_kb() -> MilvusKB:
    """连接测试 Milvus；服务不可用时由调用方跳过测试。"""
    return MilvusKB(
        f"/tmp/milvus-child-test-{uuid.uuid4().hex}",
        milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
    )


@pytest.mark.integration
def test_child_collection_round_trip_reads_schema_and_child_only():
    """真实 Milvus 回读 child schema、父子标识和 sparse 向量字段。"""
    try:
        kb = _live_child_kb()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus is unavailable: {exc}")

    collection = None
    try:
        collection = kb._create_new_child_collection(1024, sparse_enabled=True)
        fields = {field.name: field for field in collection.schema.fields}
        assert fields["dense_vector"].params["dim"] == 1024
        assert "parent_id" in fields
        assert "bge_m3_sparse_vector" in fields
        assert fields["bge_m3_sparse_vector"].nullable is False
        assert "content_sparse" in fields

        child = {
            "child_id": f"child_{uuid.uuid4().hex}",
            "parent_id": "parent_live_test",
            "file_id": "file_live_test",
            "doc_id": "doc_live_test",
            "version_id": "version_live_test",
            "child_text": "live child collection test",
            "child_index": 0,
            "metadata": {"spans": []},
        }
        asyncio.run(
            kb._insert_child_chunks_to_milvus(
                "kb_live_test",
                collection,
                [child],
                [[0.1] * 1024],
                sparse_embeddings=[{1: 0.5, 3: 0.2}],
            )
        )
        collection.flush()
        collection.load()
        rows = collection.query(
            expr='knowledge_base_id == "kb_live_test"',
            output_fields=["child_id", "parent_id", "file_id", "bge_m3_sparse_vector"],
        )
        assert rows and rows[0]["child_id"] == child["child_id"]
        assert rows[0]["parent_id"] == child["parent_id"]
        assert rows[0]["bge_m3_sparse_vector"][1] == pytest.approx(0.5)
        assert rows[0]["bge_m3_sparse_vector"][3] == pytest.approx(0.2)
        dense_hits = collection.search(
            data=[[0.1] * 1024],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=1,
            expr='knowledge_base_id == "kb_live_test"',
            output_fields=["child_id", "parent_id"],
        )
        assert dense_hits[0][0].entity.get("child_id") == child["child_id"]
        bm25_hits = collection.search(
            data=["integration child"],
            anns_field=CONTENT_SPARSE_FIELD,
            param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.0}},
            limit=1,
            expr='knowledge_base_id == "kb_live_test"',
            output_fields=["child_id", "parent_id"],
        )
        assert bm25_hits[0][0].entity.get("parent_id") == child["parent_id"]
    finally:
        if collection is not None and utility.has_collection(collection.name, using=kb.connection_alias):
            utility.drop_collection(collection.name, using=kb.connection_alias)
        kb.__del__()


def test_child_collection_rejects_missing_parent_id_before_milvus_write():
    """缺少 parent_id 的记录在本地校验阶段失败，避免父块进入 Milvus。"""
    with pytest.raises(ValueError, match="parent_id"):
        MilvusKB._build_child_entities(
            "kb",
            [{"child_id": "child", "child_text": "text"}],
            [[0.1, 0.2]],
        )


@pytest.mark.integration
def test_child_collection_schema_is_stable_when_model_sparse_is_disabled():
    """关闭模型稀疏写入时保留共享 schema，但不写伪造稀疏坐标。"""
    try:
        kb = _live_child_kb()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus is unavailable: {exc}")

    collection = None
    try:
        collection = kb._create_new_child_collection(1024, sparse_enabled=False)
        child = {
            "child_id": f"child_{uuid.uuid4().hex}",
            "parent_id": "parent_sparse_disabled",
            "file_id": "file_sparse_disabled",
            "doc_id": "doc_sparse_disabled",
            "version_id": "version_sparse_disabled",
            "child_text": "sparse disabled child",
            "child_index": 0,
        }
        asyncio.run(
            kb._insert_child_chunks_to_milvus(
                "kb_sparse_disabled",
                collection,
                [child],
                [[0.1] * 1024],
            )
        )
        collection.flush()
        collection.load()
        rows = collection.query(
            expr='knowledge_base_id == "kb_sparse_disabled"',
            output_fields=["child_id", "bge_m3_sparse_vector"],
        )
        assert rows and rows[0]["child_id"] == child["child_id"]
        assert rows[0]["bge_m3_sparse_vector"] == {}
    finally:
        if collection is not None and utility.has_collection(collection.name, using=kb.connection_alias):
            utility.drop_collection(collection.name, using=kb.connection_alias)
        kb.__del__()


def test_child_collection_rejects_invalid_sparse_vector_before_milvus_write():
    """非法 sparse 键和值在写入前失败。"""
    with pytest.raises(ValueError, match="indexes"):
        MilvusKB._validate_child_sparse_vector({"1": 0.5})
    with pytest.raises(ValueError, match="finite"):
        MilvusKB._validate_child_sparse_vector({1: float("nan")})
