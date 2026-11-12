"""MessageRepository.count_daily_activity 的消息活动聚合语义。"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from test.support.sqlite import create_utc_sqlite_engine
from yuxi.infrastructure.postgres.base import Base
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.messages import MessageRepository
from yuxi.modules.identity.models import User
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

WINDOW_DAYS = 120


@pytest_asyncio.fixture()
async def activity_db():
    """提供内存 SQLite 业务库。"""
    engine = create_utc_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _shanghai_date(moment):
    """与仓储实现一致的上海日历日换算。"""
    return (moment + timedelta(hours=8)).date().isoformat()


async def test_count_daily_activity_counts_roles_and_excludes_others(activity_db):
    db = activity_db
    db.add_all(
        [
            User(username="Alice", uid="uid-alice", password_hash="placeholder", role="user"),
            User(username="Bob", uid="uid-bob", password_hash="placeholder", role="user"),
        ]
    )
    await db.flush()

    now = utc_now()

    def make_session(uid: str, thread_id: str, status: str = "active") -> Session:
        return Session(
            thread_id=thread_id,
            project_id="p-1",
            uid=uid,
            agent_id="agent-helper",
            title="t",
            status=status,
            created_at=now,
            updated_at=now,
        )

    active = make_session("uid-alice", "thread-alice")
    deleted = make_session("uid-alice", "thread-alice-deleted", status="deleted")
    bob_session = make_session("uid-bob", "thread-bob")
    db.add_all([active, deleted, bob_session])
    await db.flush()

    included = [
        (active, Message(agent_session=active, role="user", content="q", created_at=now - timedelta(minutes=30))),
        (active, Message(agent_session=active, role="assistant", content="a", created_at=now - timedelta(hours=1))),
        (
            active,
            Message(
                agent_session=active,
                role="assistant",
                content="",
                message_type="model_audit",
                created_at=now - timedelta(hours=2),
            ),
        ),
        (
            active,
            Message(
                agent_session=active,
                role="tool",
                content="",
                message_type="tool_audit",
                created_at=now - timedelta(hours=3),
            ),
        ),
        (active, Message(agent_session=active, role="user", content="old", created_at=now - timedelta(days=2))),
    ]
    excluded = [
        (active, Message(agent_session=active, role="system", content="sys", created_at=now)),
        (deleted, Message(agent_session=deleted, role="user", content="gone", created_at=now)),
        (bob_session, Message(agent_session=bob_session, role="user", content="bob", created_at=now)),
    ]
    db.add_all([message for _, message in included + excluded])
    await db.flush()

    result = await MessageRepository(db).count_daily_activity("uid-alice", days=WINDOW_DAYS, now=now)

    assert len(result) == WINDOW_DAYS
    assert result[-1]["date"] == _shanghai_date(now)
    counted = {item["date"]: item["message_count"] for item in result if item["message_count"]}
    expected: dict[str, int] = {}
    for _, message in included:
        date = _shanghai_date(message.created_at)
        expected[date] = expected.get(date, 0) + 1
    assert counted == expected


async def test_count_daily_activity_excludes_messages_before_window(activity_db):
    db = activity_db
    db.add(User(username="Alice", uid="uid-alice", password_hash="placeholder", role="user"))
    await db.flush()

    now = utc_now()
    session_record = Session(
        thread_id="thread-alice",
        project_id="p-1",
        uid="uid-alice",
        agent_id="agent-helper",
        title="t",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(session_record)
    await db.flush()
    db.add_all(
        [
            Message(agent_session=session_record, role="user", content="recent", created_at=now - timedelta(hours=1)),
            Message(
                agent_session=session_record,
                role="user",
                content="ancient",
                created_at=now - timedelta(days=WINDOW_DAYS + 5),
            ),
        ]
    )
    await db.flush()

    result = await MessageRepository(db).count_daily_activity("uid-alice", days=WINDOW_DAYS, now=now)

    assert sum(item["message_count"] for item in result) == 1
    assert result[0]["date"] == _shanghai_date(now - timedelta(days=WINDOW_DAYS - 1))
