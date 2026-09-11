from types import SimpleNamespace

import pytest

from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.implementations.milvus import MilvusKB

pytestmark = pytest.mark.unit


class _DummyKnowledgeBase(KnowledgeBase):
    """用于测试知识库基类修复逻辑的最小替身。"""

    @property
    def kb_type(self) -> str:
        return "dummy"

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None):
        del kb_id, embedding_model_spec
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        del instance

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        del kb_id, file_id, operator_id
        return {}

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        del query_text, kb_id, kwargs
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        del kb_id, kwargs
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        del kb_id, file_id

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        return {}


def _make_file_record(**overrides):
    data = {
        "file_id": "file-1",
        "kb_id": "kb-1",
        "parent_id": None,
        "filename": "demo.md",
        "file_type": "md",
        "path": "minio://knowledgebases/kb-1/upload/demo.md",
        "markdown_file": None,
        "status": "indexed",
        "content_hash": "hash",
        "file_size": 123,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": "file",
        "processing_params": {"ocr_engine": "disable"},
        "is_folder": False,
        "error_message": None,
        "created_by": "user",
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
        "original_filename": None,
        "minio_url": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_repair_missing_file_stats_uses_parent_child_tables(monkeypatch):
    """Parent-Child 文件修复统计必须从父子表回填，不得落回旧 chunks 表。"""
    kb = _DummyKnowledgeBase("/tmp/dummy-kb")
    records = [
        _make_file_record(
            file_id="legacy-file",
            processing_params={"ocr_engine": "disable", "indexing_path": "single_chunk"},
            chunk_count=9,
            token_count=0,
        ),
        _make_file_record(
            file_id="parent-child-file",
            processing_params={
                "ocr_engine": "disable",
                "indexing_path": "parent_child",
                "parent_child": {"enabled": True},
            },
            chunk_count=7,
            token_count=0,
        ),
    ]

    class FakeFileRepo:
        def __init__(self):
            self.updated: list[tuple[str, str, dict]] = []

        async def list_by_kb_id_after(self, kb_id, after_file_id=None, limit=500, files_only=True):
            del kb_id, limit, files_only
            if after_file_id is None:
                return records
            return []

        async def update_fields(self, *, file_id: str, kb_id: str, data: dict):
            self.updated.append((file_id, kb_id, data))
            record = next(item for item in records if item.file_id == file_id)
            for key, value in data.items():
                setattr(record, key, value)
            return record

        async def get_kb_file_stats(self, kb_id: str) -> dict[str, int]:
            del kb_id
            return {
                "chunk_count": sum(int(record.chunk_count or 0) for record in records),
                "token_count": sum(int(record.token_count or 0) for record in records),
            }

    class FakeLegacyChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["legacy-file"]
            return {"legacy-file": 2}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["legacy-file"]
            return [SimpleNamespace(file_id="legacy-file", content="legacy chunk text")]

    class FakeParentChildRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["parent-child-file"]
            return {"parent-child-file": 4}

        async def sum_token_count_by_file_ids(self, file_ids):
            assert file_ids == ["parent-child-file"]
            return {"parent-child-file": 11}

    file_repo = FakeFileRepo()
    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: file_repo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository",
        lambda: FakeLegacyChunkRepo(),
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_parent_child_chunk_repository.KnowledgeParentChildChunkRepository",
        lambda: FakeParentChildRepo(),
    )
    monkeypatch.setattr("yuxi.knowledge.chunking.ragflow_like.nlp.count_tokens", lambda text: len(text.split()))

    result = await kb.repair_missing_file_stats("kb-1")

    assert file_repo.updated == [
        ("legacy-file", "kb-1", {"chunk_count": 2, "token_count": 3}),
        ("parent-child-file", "kb-1", {"chunk_count": 4, "token_count": 11}),
    ]
    assert records[0].chunk_count == 2
    assert records[0].token_count == 3
    assert records[1].chunk_count == 4
    assert records[1].token_count == 11
    assert result["updated_chunk_files"] == 2
    assert result["updated_token_files"] == 2


@pytest.mark.asyncio
async def test_get_file_content_uses_parent_child_parents(monkeypatch):
    """Parent-Child 文件内容必须从 active 父块表回读。"""
    kb = MilvusKB.__new__(MilvusKB)
    monkeypatch.setattr(
        kb,
        "_load_file_meta",
        lambda _kb_id, _file_id, refresh=False: _async_value(
            {
                "file_id": "parent-child-file",
                "processing_params": {
                    "indexing_path": "parent_child",
                    "parent_child": {"enabled": True},
                },
                "markdown_file": None,
            }
        ),
    )

    class FakeLegacyChunkRepo:
        async def list_by_file_id(self, file_id):
            raise AssertionError(f"legacy chunks should not be read for parent-child files: {file_id}")

    class FakeParentChildRepo:
        async def list_parents_by_file_id(self, file_id):
            assert file_id == "parent-child-file"
            return [
                SimpleNamespace(
                    parent_id="parent-1",
                    parent_text="parent body",
                    parent_index=0,
                    start_offset=10,
                    end_offset=42,
                    graph_indexed=True,
                    ent_ids=["e1"],
                    tags=["tag"],
                    extraction_result={"ok": True},
                )
            ]

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository",
        lambda: FakeLegacyChunkRepo(),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.KnowledgeParentChildChunkRepository",
        lambda: FakeParentChildRepo(),
    )

    result = await kb.get_file_content("kb-1", "parent-child-file")

    assert result["lines"] == [
        {
            "id": "parent-1",
            "content": "parent body",
            "chunk_order_index": 0,
            "start_char_pos": 10,
            "end_char_pos": 42,
            "start_token_pos": None,
            "end_token_pos": None,
            "graph_indexed": True,
            "ent_ids": ["e1"],
            "tags": ["tag"],
            "extraction_result": {"ok": True},
        }
    ]


async def _async_value(value):
    """返回异步测试值。"""
    return value
