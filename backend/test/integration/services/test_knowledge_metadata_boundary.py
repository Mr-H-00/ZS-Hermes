"""真实 PG/HTTP 的元数据边界；对象正文只替换存储字节读取协议。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema

from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.knowledge import management
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.migrations.schema import create_knowledge_tables
from yuxi.modules.knowledge import runtime
from yuxi.modules.knowledge.chunking.presets import ChunkConfigError
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile
from yuxi.modules.knowledge.services import documents
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
BASE = "/api/knowledge/knowledge-bases/kb-local"
MARKDOWN = "minio://kb-parsed/kb-local/parsed/file-local/content.md"


@pytest_asyncio.fixture
async def boundary(monkeypatch, tmp_path):
    """替换授权用户及对象读取，业务调用和提交均使用真实实现。"""
    schema, admin, engine, _manager = await create_isolated_manager("pytest_metadata_boundary")
    await create_knowledge_tables(_manager)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction():
        """保留服务拥有的事务提交点。"""
        async with sessions() as session, session.begin():
            yield session

    monkeypatch.setattr(pg_manager, "get_async_session_context", transaction)
    invalid_config = {"chunk_preset_id": "removed-parser-preset"}
    with pytest.raises(ChunkConfigError):
        MilvusKB.normalize_additional_params(invalid_config)
    async with sessions() as session, session.begin():
        session.add_all(
            [
                KnowledgeBase(kb_id="kb-local", name="local", kb_type="milvus", additional_params=invalid_config),
                KnowledgeBase(kb_id="kb-other", name="other", kb_type="milvus"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                KnowledgeFile(
                    file_id="file-local",
                    kb_id="kb-local",
                    filename="report.md",
                    status="parsed",
                    markdown_file=MARKDOWN,
                    path="/not-an-object/report.md",
                ),
                KnowledgeFile(file_id="folder-local", kb_id="kb-local", filename="folder", is_folder=True, status="uploaded"),
                KnowledgeFile(file_id="url-local", kb_id="kb-local", filename="page", file_type="url", status="uploaded"),
                KnowledgeFile(file_id="file-other", kb_id="kb-other", filename="secret.md", status="parsed", markdown_file=MARKDOWN),
                KnowledgeFile(file_id="folder-other", kb_id="kb-other", filename="private", is_folder=True, status="uploaded"),
            ]
        )

    def reject_executor(*_args, **_kwargs):
        """元数据路径装配执行器即暴露回归。"""
        pytest.fail("metadata operations constructed Milvus")

    async def read_object(path):
        """仅提供固定正文，不替代文件归属、窗口计算或持久查询。"""
        assert path == MARKDOWN
        return "第一行\n第二行\n第三行".encode()

    monkeypatch.setattr(MilvusKB, "__init__", reject_executor)
    monkeypatch.setattr(documents, "read_minio_bytes", read_object)
    manager = KnowledgeBaseManager(str(tmp_path))
    monkeypatch.setattr(management, "knowledge_base_manager", manager)
    monkeypatch.setattr(runtime, "knowledge_base", manager)
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(management.knowledge, prefix="/api")
    user = SimpleNamespace(uid="authorized-user", role="admin")
    app.dependency_overrides[management.require_knowledge_base_read] = lambda: user
    app.dependency_overrides[management.require_knowledge_base_manage] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, manager, sessions
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_metadata_content_and_directory_move_ignore_broken_processing_config(boundary):
    """已持久化正文与目录在执行器不可用、配置损坏时仍可读取和移动。"""
    client, manager, sessions = boundary
    response = await client.get(f"{BASE}/documents/file-local/basic")
    assert response.status_code == 200
    assert response.json()["meta"]["filename"] == "report.md"
    response = await client.get(f"{BASE}/documents/file-local/content")
    assert response.status_code == 200
    assert response.json()["content"] == "第一行\n第二行\n第三行"
    window = await manager.open_document("kb-local", "file-local", offset=1, limit=1)
    assert window["content"] == "     2\t第二行"
    assert window["file_id"] == "file-local" and window["kb_id"] == "kb-local"
    response = await client.put(f"{BASE}/documents/file-local/move", json={"new_parent_id": "folder-local"})
    assert response.status_code == 200
    async with sessions() as session:
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file-local"))
        assert file.parent_id == "folder-local" and file.kb_id == "kb-local"
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "kb-local"))
        assert kb.additional_params["chunk_preset_id"] == "removed-parser-preset"


async def test_cross_kb_file_and_parent_are_rejected_without_mutating_records(boundary):
    """路由授权通过后仍由文件归属和目录仓储拒绝跨库身份。"""
    client, manager, sessions = boundary
    for suffix in ("basic", "content"):
        response = await client.get(f"{BASE}/documents/file-other/{suffix}")
        assert response.status_code == 404
        assert "secret.md" not in response.text and "第一行" not in response.text
    with pytest.raises(ValueError, match="不存在"):
        await manager.open_document("kb-local", "file-other")
    for file_id, parent_id in (("file-other", "folder-local"), ("file-local", "folder-other")):
        response = await client.put(f"{BASE}/documents/{file_id}/move", json={"new_parent_id": parent_id})
        assert response.status_code == 404
    async with sessions() as session:
        files = list((await session.scalars(select(KnowledgeFile).where(KnowledgeFile.is_folder.is_(False)))).all())
        assert all(file.parent_id is None and file.deleted_at is None for file in files)


@pytest.mark.parametrize("file_id", ["folder-local", "url-local", "file-local"])
async def test_original_download_invalid_sources_keep_http_400(boundary, file_id):
    """文件夹、URL 和非对象路径继续返回公开的 400 错误协议。"""
    client, _, _ = boundary
    response = await client.get(f"{BASE}/documents/{file_id}/download")
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("batch", [False, True])
async def test_missing_document_deletion_keeps_http_400_and_existing_data(boundary, batch):
    """单项缺失与批量全失败保留 400，且没有删除副作用。"""
    client, _, sessions = boundary
    if batch:
        response = await client.request("DELETE", f"{BASE}/documents/batch", json=["missing", "file-other"])
        assert "所有 2 个文件均未删除" in response.json()["detail"]
    else:
        response = await client.delete(f"{BASE}/documents/missing")
    assert response.status_code == 400
    async with sessions() as session:
        files = list((await session.scalars(select(KnowledgeFile))).all())
        assert len(files) == 5
        assert all(file.deleted_at is None for file in files)
