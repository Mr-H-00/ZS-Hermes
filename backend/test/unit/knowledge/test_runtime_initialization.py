from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from yuxi.knowledge.manager import KnowledgeBaseManager

pytestmark = pytest.mark.unit


def test_knowledge_runtime_preserves_lite_mode(tmp_path):
    """直接导入知识 runtime 时，LITE 也不得注册 Milvus 后端。"""

    env = os.environ.copy()
    env["LITE_MODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from yuxi.knowledge.runtime import knowledge_base; "
                "from yuxi.knowledge.factory import KnowledgeBaseFactory; "
                "print(json.dumps({"
                "'manager': type(knowledge_base).__name__, "
                "'types': sorted(KnowledgeBaseFactory.get_available_types())"
                "}))"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    loaded = json.loads(result.stdout.splitlines()[-1])
    assert loaded == {"manager": "KnowledgeBaseManager", "types": ["dify", "notion"]}


@pytest.mark.asyncio
async def test_initialize_creates_executors_for_types_in_use(monkeypatch, tmp_path):
    """initialize() 只为数据库中实际使用的知识库类型创建执行器。"""
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [
            SimpleNamespace(kb_id="kb_1", kb_type="milvus"),
        ]

    fake_instance = SimpleNamespace()

    def fake_create(_kb_type, _work_dir):
        return fake_instance

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fake_create),
    )

    await manager.initialize()

    assert manager.kb_instances == {"milvus": fake_instance}


@pytest.mark.asyncio
async def test_initialize_propagates_failure_from_backend_already_in_use(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_1", kb_type="milvus")]

    def fail_create(_kb_type, _work_dir):
        raise ConnectionError("milvus unavailable")

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fail_create),
    )

    with pytest.raises(RuntimeError, match="milvus:ConnectionError"):
        await manager.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_persisted_unsupported_backend(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_legacy", kb_type="removed-backend")]

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )

    with pytest.raises(RuntimeError, match="removed-backend:unsupported"):
        await manager.initialize()
