"""真实 PostgreSQL 和 Redis 下的统计投影回归。"""

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.repositories import knowledge_base_repository, knowledge_file_repository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.knowledge_folder_service import KnowledgeFolderService
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeFile
from yuxi.storage.redis import close_async_redis_client, get_async_redis_client
from server.routers import knowledge_router

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """只使用独立 Schema。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """测试资源由局部 fixture 清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """本测试不创建沙盒。"""
    yield


@pytest.fixture
async def stats_store(monkeypatch):
    """创建隔离表和唯一缓存键。"""
    schema = f"pytest_stats_{uuid.uuid4().hex}"
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        """提交或回滚一个独立事务。"""
        async with sessions.begin() as session:
            yield session

    monkeypatch.setattr(knowledge_base_repository.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(knowledge_file_repository.pg_manager, "get_async_session_context", session_context)
    redis = await get_async_redis_client()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeBase.__table__.create)
            await connection.run_sync(KnowledgeFile.__table__.create)
            await connection.run_sync(KnowledgeChunk.__table__.create)
        async with sessions.begin() as session:
            session.add(
                KnowledgeBase(kb_id=schema, name="stats regression", kb_type="milvus", additional_params={"keep": True})
            )
            session.add(
                KnowledgeFile(
                    file_id=schema,
                    kb_id=schema,
                    filename="test.md",
                    is_folder=False,
                    status="parsed",
                    chunk_count=0,
                    token_count=0,
                )
            )
        yield schema, sessions, redis
    finally:
        await redis.delete(f"yuxi:kb_file_stats:{schema}", f"yuxi:knowledge_base:{schema}:lock")
        await close_async_redis_client()
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_query_stats_treats_legacy_null_folder_flag_as_file(stats_store):
    """历史空值目录标记仍计入文件数量、容量、内容量和状态桶。"""
    kb_id, sessions, _ = stats_store
    repository = KnowledgeFileRepository()
    async with sessions.begin() as session:
        file = (await session.execute(select(KnowledgeFile))).scalar_one()
        file.is_folder = None
        file.status = "uploaded"
        file.file_size = 37
        file.chunk_count = 3
        file.token_count = 29

    async with sessions() as session:
        stats = await repository.query_kb_file_stats(kb_id, session=session)
    dashboard_stats = await repository.aggregate_dashboard_stats()

    assert stats == {
        "row_count": 1,
        "file_count": 1,
        "folder_count": 0,
        "total_size": 37,
        "chunk_count": 3,
        "token_count": 29,
        "pending_parse_count": 1,
        "pending_index_count": 0,
        "processing_count": 0,
    }
    assert dashboard_stats == [("unknown", 1, 37, 3)]


async def test_virtual_folder_migration_refreshes_persisted_and_cached_stats(stats_store):
    """路径迁移完成后持久投影和预热缓存立即反映新增目录。"""
    kb_id, sessions, redis = stats_store
    file_repository = KnowledgeFileRepository()
    async with sessions.begin() as session:
        file = (await session.execute(select(KnowledgeFile))).scalar_one()
        file.filename = "docs/test.md"

    primed = await file_repository.get_kb_file_stats(kb_id)
    assert primed["row_count"] == 1
    assert primed["folder_count"] == 0
    await redis.expire(f"yuxi:kb_file_stats:{kb_id}", 300)

    context = AsyncMock()

    async def run_owned_transaction(operation):
        """接收迁移操作，在隔离 Schema 的真实事务中执行且不返回结果。"""
        async with sessions.begin() as session:
            await operation(session, object())

    context.run_owned_transaction.side_effect = run_owned_transaction
    result = await KnowledgeFolderService(
        file_repository,
        KnowledgeBaseRepository(),
    ).migrate_virtual_folder_data(
        context,
        kb_id=kb_id,
        operator_id="user-1",
    )

    async with sessions() as session:
        knowledge_base = (await session.execute(select(KnowledgeBase))).scalar_one()
        files = list((await session.execute(select(KnowledgeFile))).scalars().all())
    persisted = knowledge_base.additional_params["stats"]
    assert persisted["row_count"] == 2
    assert persisted["file_count"] == 1
    assert persisted["folder_count"] == 1
    assert sorted((file.filename, file.is_folder) for file in files) == [("docs", True), ("test.md", False)]
    assert result == {
        "processed_steps": 1,
        "created_folders": 1,
        "conflict_files": 0,
        "remaining_files": 0,
    }

    refreshed = await file_repository.get_kb_file_stats(kb_id)
    assert refreshed == persisted
    assert refreshed != primed


@pytest.mark.parametrize("fails", [False, True])
async def test_operation_refresh_ignores_cached_stats(stats_store, tmp_path, fails):
    """成功与异常收尾都必须写回真实结果，读缓存可继续复用。"""
    kb_id, sessions, redis = stats_store
    cached = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    assert cached["pending_index_count"] == 1
    # 延长本测试缓存存活期，消除慢 CI 下自然过期导致的假通过。
    await redis.expire(f"yuxi:kb_file_stats:{kb_id}", 300)
    manager = KnowledgeBaseManager(str(tmp_path))

    async def operation():
        """模拟已提交的文件索引结果。"""
        await KnowledgeFileRepository().update_fields(
            file_id=kb_id,
            kb_id=kb_id,
            data={"status": "indexed", "chunk_count": 158, "token_count": 900},
        )
        if fails:
            raise ValueError("operation failed")
        return "indexed"

    if fails:
        with pytest.raises(ValueError, match="operation failed"):
            await manager._run_with_stats_refresh(kb_id, operation())
    else:
        assert await manager._run_with_stats_refresh(kb_id, operation()) == "indexed"
    async with sessions() as session:
        row = (await session.execute(select(KnowledgeBase))).scalar_one()
        assert row.additional_params["keep"] is True
        assert row.additional_params["stats"]["chunk_count"] == 158
        assert row.additional_params["stats"]["token_count"] == 900
        assert row.additional_params["stats"]["pending_index_count"] == 0
    summaries = await manager.get_databases()
    assert len(summaries) == 1
    assert summaries[0].chunk_count == 158
    assert summaries[0].pending_index_count == 0
    assert await KnowledgeFileRepository().get_kb_file_stats(kb_id) == cached


async def test_refresh_queries_after_acquiring_row_lock(stats_store, tmp_path):
    """等待行锁期间的文件提交必须进入最终聚合。"""
    kb_id, sessions, _ = stats_store
    manager = KnowledgeBaseManager(str(tmp_path))
    async with sessions.begin() as blocker:
        await blocker.execute(select(KnowledgeBase).with_for_update())
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(manager._refresh_database_stats(kb_id))
        try:
            async with asyncio.timeout(10):
                while True:
                    if task.done():
                        task.result()
                    async with sessions() as observer:
                        waiting = await observer.scalar(
                            text("SELECT count(*) FROM pg_stat_activity WHERE :blocker = ANY(pg_blocking_pids(pid))"),
                            {"blocker": blocker_pid},
                        )
                    if waiting:
                        break
                    await asyncio.sleep(0.02)
            await KnowledgeFileRepository().update_fields(
                file_id=kb_id,
                kb_id=kb_id,
                data={"status": "indexed", "chunk_count": 7, "token_count": 42},
            )
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    result = await asyncio.wait_for(task, timeout=10)
    assert result["chunk_count"] == 7
    assert result["pending_index_count"] == 0
    row = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    assert row.additional_params["stats"] == result


async def test_repair_returns_fresh_persisted_stats(stats_store, tmp_path, monkeypatch):
    """文件缺失统计修复后，响应与持久投影都忽略旧缓存。"""
    kb_id, sessions, redis = stats_store
    await KnowledgeFileRepository().get_kb_file_stats(kb_id)
    await redis.expire(f"yuxi:kb_file_stats:{kb_id}", 300)
    async with sessions.begin() as session:
        row = (await session.execute(select(KnowledgeFile))).scalar_one()
        row.status = "indexed"
        row.file_size = 5
        session.add(KnowledgeChunk(kb_id=kb_id, file_id=kb_id, chunk_id=kb_id, chunk_index=0, content="hello"))
    manager = KnowledgeBaseManager(str(tmp_path))
    # 统计修复只使用 PG；避开与本测试无关的 Milvus 连接初始化。
    executor = object.__new__(MilvusKB)
    monkeypatch.setattr(manager, "get_kb_executor", AsyncMock(return_value=executor))
    monkeypatch.setattr(knowledge_router, "knowledge_base", manager)
    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    # 本用例验证 HTTP 统计契约；权限规则由知识库权限 integration 拥有。
    app.dependency_overrides[knowledge_router.require_knowledge_base_manage] = lambda: object()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        serving.result()
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}") as client:
                response = await client.post(f"/api/knowledge/databases/{kb_id}/stats/repair")
            assert response.status_code == 200, response.text
            result = response.json()
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
    assert result["updated_files"] == 1
    assert result["stats"]["chunk_count"] == 1
    assert result["stats"]["pending_index_count"] == 0
    row = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    file = await KnowledgeFileRepository().get_by_file_id(kb_id)
    assert row.additional_params["stats"] == result["stats"]
    assert file.chunk_count == 1
    assert file.token_count > 0
    assert result["stats"]["token_count"] == file.token_count
