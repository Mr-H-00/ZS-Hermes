from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.conversation_repository import (
    ConversationRepository,
    INVOCATION_CONVERSATION_SOURCES,
    MAX_CONVERSATION_TITLE_LENGTH,
)
from yuxi.storage.postgres.models_business import Base, Conversation, Message
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture()
async def conversation_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def test_normalize_title_truncates_when_too_long():
    repo = ConversationRepository(None)  # type: ignore[arg-type]
    raw = "a" * (MAX_CONVERSATION_TITLE_LENGTH + 50)

    normalized = repo._normalize_title(raw)

    assert normalized is not None
    assert len(normalized) == MAX_CONVERSATION_TITLE_LENGTH
    assert normalized == "a" * MAX_CONVERSATION_TITLE_LENGTH


def test_normalize_title_trims_spaces():
    repo = ConversationRepository(None)  # type: ignore[arg-type]

    normalized = repo._normalize_title("   hello world   ")

    assert normalized == "hello world"


@pytest.mark.asyncio
async def test_lock_conversation_refreshes_cached_lifecycle_state(tmp_path):
    """加锁读取必须刷新同一 Session 中已缓存的生命周期状态。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conversation-lock.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as reader, factory() as writer:
            conversation = Conversation(
                thread_id="thread-refresh-lock",
                project_id="project-refresh-lock",
                uid="user-a",
                agent_id="agent-a",
                title="Refresh lock",
                status="active",
            )
            reader.add(conversation)
            await reader.commit()

            repository = ConversationRepository(reader)
            cached = await repository.get_conversation_by_id(conversation.id)
            assert cached is conversation
            assert cached.status == "active"

            await writer.execute(
                update(Conversation).where(Conversation.id == conversation.id).values(status="deleted")
            )
            await writer.commit()

            locked = await repository.lock_conversation_by_thread_id(conversation.thread_id)

            assert locked is conversation
            assert locked.status == "deleted"
    finally:
        await engine.dispose()


def _seed_invocation_excluding_conversations() -> tuple[Conversation, Conversation, Conversation, datetime]:
    now = utc_now_naive()
    normal = Conversation(
        thread_id="thread-normal",
        project_id="project-thread-normal",
        uid="user-a",
        agent_id="agent-a",
        title="Normal",
        status="active",
        created_at=now,
        updated_at=now,
        extra_metadata={},
    )
    agent_call = Conversation(
        thread_id="thread-call",
        project_id="project-thread-call",
        uid="user-a",
        agent_id="agent-a",
        title="Agent Call Run",
        status="active",
        is_pinned=True,
        created_at=now,
        updated_at=now + timedelta(minutes=2),
        extra_metadata={"source": "agent_call"},
    )
    agent_eval = Conversation(
        thread_id="thread-eval",
        project_id="project-thread-eval",
        uid="user-a",
        agent_id="agent-a",
        title="Agent Evaluation Run",
        status="active",
        created_at=now,
        updated_at=now + timedelta(minutes=1),
        extra_metadata={"source": "agent_evaluation"},
    )
    return normal, agent_call, agent_eval, now


@pytest.mark.asyncio
async def test_list_conversations_excludes_invocation_sources(conversation_session):
    normal, agent_call, agent_eval, _ = _seed_invocation_excluding_conversations()
    conversation_session.add_all([normal, agent_call, agent_eval])
    await conversation_session.commit()

    repo = ConversationRepository(conversation_session)
    items = await repo.list_conversations(
        uid="user-a",
        limit=20,
        offset=0,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    assert [item.thread_id for item in items] == ["thread-normal"]


@pytest.mark.asyncio
async def test_list_conversations_paginates_only_non_pinned_items(conversation_session):
    now = utc_now_naive()
    pinned = Conversation(
        thread_id="thread-pinned",
        project_id="project-pinned",
        uid="user-a",
        agent_id="agent-a",
        title="Pinned",
        status="active",
        is_pinned=True,
        created_at=now,
        updated_at=now + timedelta(minutes=10),
    )
    regular = [
        Conversation(
            thread_id=f"thread-{index}",
            project_id=f"project-{index}",
            uid="user-a",
            agent_id="agent-a",
            title=f"Thread {index}",
            status="active",
            created_at=now,
            updated_at=now + timedelta(minutes=index),
        )
        for index in range(4)
    ]
    conversation_session.add_all([pinned, *regular])
    await conversation_session.commit()

    repository = ConversationRepository(conversation_session)
    first_page = await repository.list_conversations(uid="user-a", limit=2, offset=0)
    second_page = await repository.list_conversations(uid="user-a", limit=2, offset=2)

    assert [item.thread_id for item in first_page] == ["thread-pinned", "thread-3", "thread-2"]
    assert [item.thread_id for item in second_page] == ["thread-pinned", "thread-1", "thread-0"]


@pytest.mark.asyncio
async def test_search_conversations_by_message_content_filters_user_status_and_tool_messages(conversation_session):
    now = utc_now_naive()
    active = Conversation(
        thread_id="thread-active",
        project_id="project-thread-active",
        uid="user-a",
        agent_id="agent-a",
        title="Active Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    deleted = Conversation(
        thread_id="thread-deleted",
        project_id="project-thread-deleted",
        uid="user-a",
        agent_id="agent-a",
        title="Deleted Thread",
        status="deleted",
        created_at=now,
        updated_at=now,
    )
    other_user = Conversation(
        thread_id="thread-other-user",
        project_id="project-thread-other-user",
        uid="user-b",
        agent_id="agent-a",
        title="Other User Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    tool_only = Conversation(
        thread_id="thread-tool-only",
        project_id="project-thread-tool-only",
        uid="user-a",
        agent_id="agent-a",
        title="Tool Only Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    conversation_session.add_all([active, deleted, other_user, tool_only])
    await conversation_session.flush()
    conversation_session.add_all(
        [
            Message(
                conversation=active,
                role="assistant",
                content="大陆部署方案需要保留",
                message_type="text",
                created_at=now,
            ),
            Message(
                conversation=deleted,
                role="assistant",
                content="大陆 deleted should not show",
                message_type="text",
                created_at=now,
            ),
            Message(
                conversation=other_user,
                role="assistant",
                content="大陆 other user should not show",
                message_type="text",
                created_at=now,
            ),
            Message(
                conversation=tool_only,
                role="tool",
                content="大陆 tool output should not show",
                message_type="tool_result",
                created_at=now,
            ),
        ]
    )
    await conversation_session.commit()

    repo = ConversationRepository(conversation_session)
    items, has_more = await repo.search_conversations_by_message_content(
        uid="user-a",
        query="大陆",
        limit=20,
        offset=0,
    )

    assert has_more is False
    assert [item["conversation"].thread_id for item in items] == ["thread-active"]
    assert items[0]["matched_count"] == 1
    assert items[0]["message_id"] is not None
    assert "大陆部署方案" in items[0]["snippets"][0]["content"]


@pytest.mark.asyncio
async def test_search_conversations_by_message_content_excludes_invocation_sources(conversation_session):
    normal, agent_call, agent_eval, now = _seed_invocation_excluding_conversations()
    conversation_session.add_all([normal, agent_call, agent_eval])
    await conversation_session.flush()
    conversation_session.add_all(
        [
            Message(conversation=normal, role="user", content="导航隐藏检查", message_type="text", created_at=now),
            Message(
                conversation=agent_call,
                role="user",
                content="导航隐藏检查 call",
                message_type="text",
                created_at=now,
            ),
            Message(
                conversation=agent_eval,
                role="user",
                content="导航隐藏检查 eval",
                message_type="text",
                created_at=now,
            ),
        ]
    )
    await conversation_session.commit()

    repo = ConversationRepository(conversation_session)
    items, has_more = await repo.search_conversations_by_message_content(
        uid="user-a",
        query="导航隐藏检查",
        limit=20,
        offset=0,
        exclude_sources=INVOCATION_CONVERSATION_SOURCES,
    )

    assert has_more is False
    assert [item["conversation"].thread_id for item in items] == ["thread-normal"]


@pytest.mark.asyncio
async def test_search_conversations_by_message_content_filters_agent_and_paginates(conversation_session):
    now = utc_now_naive()
    old = now - timedelta(days=1)
    first = Conversation(
        thread_id="thread-first",
        project_id="project-thread-first",
        uid="user-a",
        agent_id="agent-a",
        title="First",
        status="active",
        created_at=old,
        updated_at=old,
    )
    second = Conversation(
        thread_id="thread-second",
        project_id="project-thread-second",
        uid="user-a",
        agent_id="agent-a",
        title="Second",
        status="active",
        created_at=now,
        updated_at=now,
    )
    other_agent = Conversation(
        thread_id="thread-other-agent",
        project_id="project-thread-other-agent",
        uid="user-a",
        agent_id="agent-b",
        title="Other Agent",
        status="active",
        created_at=now,
        updated_at=now,
    )
    conversation_session.add_all([first, second, other_agent])
    await conversation_session.flush()
    conversation_session.add_all(
        [
            Message(
                conversation=first,
                role="user",
                content="大陆关键词 old",
                message_type="text",
                created_at=old,
            ),
            Message(
                conversation=second,
                role="assistant",
                content="大陆关键词 latest",
                message_type="text",
                created_at=now,
            ),
            Message(
                conversation=other_agent,
                role="assistant",
                content="大陆关键词 other agent",
                message_type="text",
                created_at=now,
            ),
        ]
    )
    await conversation_session.commit()

    repo = ConversationRepository(conversation_session)
    first_page, has_more = await repo.search_conversations_by_message_content(
        uid="user-a",
        agent_id="agent-a",
        query="大陆",
        limit=1,
        offset=0,
    )
    second_page, second_has_more = await repo.search_conversations_by_message_content(
        uid="user-a",
        agent_id="agent-a",
        query="大陆",
        limit=1,
        offset=1,
    )

    assert has_more is True
    assert [item["conversation"].thread_id for item in first_page] == ["thread-second"]
    assert second_has_more is False
    assert [item["conversation"].thread_id for item in second_page] == ["thread-first"]
