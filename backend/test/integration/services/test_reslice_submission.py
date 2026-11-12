"""真实 PostgreSQL 中的重切准入、同意图去重和跨文件隔离。"""

import asyncio

import pytest
from sqlalchemy import func, select

import yuxi.modules.knowledge.services.reslice as reslice
from test.integration.services.test_background_job_repository import background_job_schema as background_job_schema
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile
from yuxi.shared.errors import ResourceNotFoundError, StateConflictError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


@pytest.fixture
async def reslice_database(background_job_schema, monkeypatch):
    """用独立 Schema 真实提交，派发时独立回读已提交作业。"""
    manager = background_job_schema
    monkeypatch.setattr(reslice, "pg_manager", manager)
    published = []

    async def publish(job_id):
        """队列替身只核对事务外可见事实，不介入持久去重。"""
        async with manager.get_async_session_context() as session:
            assert await session.get(BackgroundJobRecord, job_id) is not None
        published.append(job_id)

    monkeypatch.setattr(reslice, "dispatch_job", publish)
    async with manager.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id="kb", name="reslice", kb_type="milvus"))
        await session.flush()
        session.add_all(
            [
                KnowledgeFile(
                    file_id=file_id,
                    kb_id="kb",
                    filename=file_id + ".md",
                    is_folder=False,
                    markdown_file="minio://parsed/body",
                    status="indexed",
                )
                for file_id in ("a", "b")
            ]
        )
    return manager, published


async def test_concurrent_equivalent_reslice_commits_one_job_before_dispatch(reslice_database):
    """两个并发相同请求只有一个持久作业，输入顺序不改变去重身份。"""
    manager, published = reslice_database
    first, second = await asyncio.gather(
        reslice.submit_reslice("kb", ["a", "b"], {}, "owner"),
        reslice.submit_reslice("kb", ["b", "a"], {}, "owner"),
    )
    assert first["job_id"] == second["job_id"]
    assert published == [first["job_id"]]
    async with manager.get_async_session_context() as session:
        assert await session.scalar(select(func.count()).select_from(BackgroundJobRecord)) == 1


async def test_overlapping_reslice_rejects_changed_intent_and_allows_other_file(reslice_database):
    """改参数的重叠意图被拒绝，其他文档仍可以独立登记。"""
    _manager, published = reslice_database
    first = await reslice.submit_reslice("kb", ["a"], {}, "owner")
    with pytest.raises(StateConflictError, match="正在执行的重切"):
        await reslice.submit_reslice("kb", ["a"], {"chunk_size": 100}, "owner")
    second = await reslice.submit_reslice("kb", ["b"], {}, "owner")
    assert first["job_id"] != second["job_id"]
    assert len(published) == 2
    with pytest.raises(ResourceNotFoundError):
        await reslice.submit_reslice("kb", ["missing"], {}, "owner")
