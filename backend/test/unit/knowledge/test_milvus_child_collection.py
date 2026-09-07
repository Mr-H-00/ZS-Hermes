from __future__ import annotations

from types import SimpleNamespace

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

from yuxi.knowledge.implementations.milvus import (
    CHILD_COLLECTION_PREFIX,
    CHILD_DENSE_FIELD,
    CHILD_KB_FIELD,
    CHILD_SPARSE_FIELD,
    CHILD_TEXT_FIELD,
    CONTENT_SPARSE_FIELD,
    MilvusKB,
)
import yuxi.knowledge.implementations.milvus as milvus_module


def _child_schema(dimension: int = 4, include_bge_sparse: bool = True) -> CollectionSchema:
    """构造用于 schema 校验单测的完整 child collection 定义。"""
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
        FieldSchema(name=CHILD_KB_FIELD, dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="version_id", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="child_id", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(name="parent_id", dtype=DataType.VARCHAR, max_length=100),
        FieldSchema(
            name=CHILD_TEXT_FIELD,
            dtype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        ),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="meta_info", dtype=DataType.JSON),
        FieldSchema(name=CHILD_DENSE_FIELD, dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    if include_bge_sparse:
        fields.append(FieldSchema(name=CHILD_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR))
    fields.append(FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR))
    return CollectionSchema(
        fields=fields,
        functions=[
            Function(
                name="child_text_bm25",
                input_field_names=[CHILD_TEXT_FIELD],
                output_field_names=[CONTENT_SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        ],
    )


def test_child_collection_name_is_dimension_scoped():
    """child collection 使用固定前缀和向量维度命名。"""
    assert MilvusKB._child_collection_name(1024) == f"{CHILD_COLLECTION_PREFIX}1024"
    with pytest.raises(ValueError, match="positive"):
        MilvusKB._child_collection_name(0)
    with pytest.raises(ValueError, match="integer"):
        MilvusKB._child_collection_name(1.5)


def test_child_schema_rejects_wrong_dimension_and_missing_sparse_field():
    """维度错误或声明启用 sparse 但字段缺失时 fail closed。"""
    wrong_dimension = type("Collection", (), {"schema": _child_schema(dimension=3)})()
    with pytest.raises(ValueError, match="dimension mismatch"):
        MilvusKB._validate_child_collection_schema(wrong_dimension, 4, sparse_enabled=False)

    missing_sparse = type("Collection", (), {"schema": _child_schema(include_bge_sparse=False)})()
    with pytest.raises(ValueError, match="bge_m3_sparse_vector"):
        MilvusKB._validate_child_collection_schema(missing_sparse, 4, sparse_enabled=True)


def test_child_entity_builder_rejects_parentless_and_invalid_sparse_records():
    """父块记录和非法 sparse 记录均在 SDK 调用前失败。"""
    with pytest.raises(ValueError, match="parent_id"):
        MilvusKB._build_child_entities(
            "kb",
            [{"child_id": "child", "child_text": "text"}],
            [[0.1, 0.2]],
        )
    with pytest.raises(ValueError, match="finite"):
        MilvusKB._build_child_entities(
            "kb",
            [{"child_id": "child", "parent_id": "parent", "child_text": "text"}],
            [[0.1, 0.2]],
            [{1: float("inf")}],
        )


@pytest.mark.asyncio
async def test_collection_selector_keeps_legacy_single_collection_path():
    """关闭 Parent-Child 时只调用旧单层 collection，保持旧数据可查。"""
    kb = MilvusKB.__new__(MilvusKB)
    legacy = object()
    child_calls = []

    async def get_legacy(kb_id, embedding_model_spec):
        return legacy

    async def get_child(embedding_model_spec, *, sparse_enabled):
        child_calls.append((embedding_model_spec, sparse_enabled))
        return object()

    kb._get_or_create_milvus_collection = get_legacy
    kb._get_or_create_child_collection = get_child

    result = await kb._get_or_create_collection_for_config(
        "kb",
        "provider:model",
        {"parent_child": {"enabled": False}},
    )

    assert result is legacy
    assert child_calls == []


@pytest.mark.asyncio
async def test_child_collection_rejects_sparse_for_non_bge_model(monkeypatch):
    """非 BGE-M3 模型不能通过 child collection 开启模型稀疏向量。"""
    kb = MilvusKB.__new__(MilvusKB)
    with pytest.raises(ValueError, match="BGE-M3"):
        await kb._get_or_create_child_collection("provider:Qwen/Qwen3-Embedding-0.6B", sparse_enabled=True)


@pytest.mark.asyncio
async def test_collection_selector_uses_only_dimension_child_path_when_enabled():
    """开启 Parent-Child 时不触碰旧单层 collection。"""
    kb = MilvusKB.__new__(MilvusKB)
    child = object()
    legacy_calls = []

    async def get_legacy(kb_id, embedding_model_spec):
        legacy_calls.append((kb_id, embedding_model_spec))
        return object()

    async def get_child(embedding_model_spec, *, sparse_enabled):
        assert embedding_model_spec == "provider:model"
        assert sparse_enabled is True
        return child

    kb._get_or_create_milvus_collection = get_legacy
    kb._get_or_create_child_collection = get_child

    result = await kb._get_or_create_collection_for_config(
        "kb",
        "provider:model",
        {
            "parent_child": {"enabled": True},
            "embedding_features": {"bge_m3_sparse_enabled": True},
        },
    )

    assert result is child
    assert legacy_calls == []


@pytest.mark.asyncio
async def test_child_insert_without_model_sparse_writes_no_fake_coordinate():
    """关闭模型 sparse 时只写 Milvus 所需空向量，不伪造零权重坐标。"""
    inserted = []

    class FakeCollection:
        schema = _child_schema()

        def insert(self, entities):
            inserted.append(entities)

    kb = MilvusKB.__new__(MilvusKB)
    await kb._insert_child_chunks_to_milvus(
        "kb",
        FakeCollection(),
        [{"child_id": "child", "parent_id": "parent", "child_text": "text"}],
        [[0.1, 0.2, 0.3, 0.4]],
    )

    assert inserted[0][-1] == [{}]
    assert {0: 0.0} not in inserted[0][-1]


@pytest.mark.asyncio
async def test_legacy_collection_is_kept_for_vector_query_compatibility(monkeypatch):
    """旧单层集合缺少 BM25 时不删除，继续交给旧向量查询路径。"""
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    legacy = type(
        "LegacyCollection", (), {"description": "Knowledge base using legacy-model", "schema": _child_schema()}
    )()
    dropped = []

    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(milvus_module, "Collection", lambda **_kwargs: legacy)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda _spec: SimpleNamespace(model_type="embedding", model_id="legacy-model", dimension=4),
    )
    monkeypatch.setattr(milvus_module.utility, "drop_collection", lambda *args, **kwargs: dropped.append(args))

    result = await kb._create_kb_instance("legacy-kb", "provider:model")

    assert result is legacy
    assert dropped == []
