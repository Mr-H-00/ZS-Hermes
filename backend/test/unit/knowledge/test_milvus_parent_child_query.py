from types import SimpleNamespace

import pytest

from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.read_models import KnowledgeBaseConfig


class _Hit:
    """提供 Parent-Child 查询所需的最小 Milvus hit。"""

    def __init__(self, child_id: str, parent_id: str, score: float):
        self.distance = score
        self.entity = {
            "child_id": child_id,
            "parent_id": parent_id,
            "file_id": "file-1",
            "version_id": "version-1",
            "child_text": f"text {child_id}",
            "chunk_index": int(child_id[-1]),
            "meta_info": {"spans": [{"page_num": 1}]},
        }


class _Collection:
    """记录查询并返回两个属于同一父块的子块。"""

    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return [[_Hit("child-1", "parent-1", 0.9), _Hit("child-2", "parent-1", 0.8)]]


@pytest.mark.asyncio
async def test_parent_child_query_deduplicates_parent_and_hydrates_text(monkeypatch):
    """子块负责召回，同父块命中只返回一次完整父块正文。"""
    kb = MilvusKB.__new__(MilvusKB)
    collection = _Collection()
    kb._get_or_create_collection_for_config = lambda *_args: _async_value(collection)
    kb._build_file_name_expr = lambda *_args: _async_value(None)
    kb._hydrate_chunk_sources = lambda _kb_id, chunks: _hydrate(chunks)
    fake_model = SimpleNamespace(batch_encode=lambda _texts, **_kwargs: [[0.1, 0.2]])
    monkeypatch.setattr("yuxi.models.embed.select_embedding_model", lambda _spec: fake_model)

    async def list_parents(_self, parent_ids):
        assert parent_ids == ["parent-1"]
        return [
            SimpleNamespace(
                parent_id="parent-1",
                parent_text="complete parent text",
                file_id="file-1",
                doc_id="doc-1",
                version_id="version-1",
                parent_index=0,
            )
        ]

    async def list_children(_self, child_ids):
        return [
            SimpleNamespace(
                child_id=child_id,
                parent_id="parent-1",
                kb_id="kb-1",
                version_id="version-1",
                child_index=index,
                start_offset=index * 10,
                end_offset=index * 10 + 8,
                spans=[{"page_num": index + 1}],
            )
            for index, child_id in enumerate(child_ids)
        ]

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.KnowledgeParentChildChunkRepository.list_parents_by_ids",
        list_parents,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.KnowledgeParentChildChunkRepository.list_children_by_ids",
        list_children,
    )
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.get_cached_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.cache_parent", lambda *_args: _async_value(None))
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.get_cached_query", lambda *_args: _async_value(None))
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.cache_query", lambda *_args: _async_value(None))
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.KnowledgeParentChildChunkRepository.list_active_version_ids",
        lambda *_args: _async_value(["version-1"]),
    )
    config = KnowledgeBaseConfig(
        kb_id="kb-1",
        kb_type="milvus",
        embedding_model_spec="provider:model",
        additional_params={"parent_child": {"enabled": True}},
    )

    result = await kb.aquery("query", "kb-1", config=config, top_k_child=2, top_k_parent=1)

    assert len(result) == 1
    assert result[0]["content"] == "complete parent text"
    assert result[0]["score"] == pytest.approx(0.9)
    assert result[0]["metadata"]["child_id"] == "child-1"
    assert len(result[0]["child_hits"]) == 2
    assert collection.calls[0]["anns_field"] == "dense_vector"
    assert collection.calls[0]["limit"] == 2


def test_parent_child_score_helpers_cover_equal_scores_and_rrf():
    """等分候选归一化稳定，RRF 固定常数为 60。"""
    candidates = [{"child_id": "a", "score": 2.0}, {"child_id": "b", "score": 2.0}]
    assert MilvusKB._min_max_normalize(candidates) == {"a": 1.0, "b": 1.0}
    fused = MilvusKB._rrf_candidates([[{"child_id": "a", "score": 0.0}]])
    assert fused[0]["score"] == pytest.approx(1 / 61)


async def _async_value(value):
    """返回异步替身结果。"""
    return value


async def _hydrate(chunks):
    """模拟来源信息回填。"""
    for chunk in chunks:
        chunk["metadata"]["source"] = "demo.md"
