from types import SimpleNamespace

import pytest

from yuxi.repositories.tool_message_audit_repository import ToolMessageAuditRepository
from yuxi.storage.postgres.models_business import AgentRun


class _FakeDb:
    def __init__(self, runs):
        self.runs = runs

    async def get(self, model, run_id):
        assert model is AgentRun
        return self.runs.get(run_id)


class _FakeTimelineResult:
    def __init__(self, messages):
        self.messages = messages

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self.messages


class _FakeTimelineDb:
    def __init__(self, messages):
        self.messages = messages
        self.query = None

    async def execute(self, query):
        self.query = query
        return _FakeTimelineResult(self.messages)


@pytest.mark.asyncio
async def test_resume_source_run_ids_follow_full_same_conversation_ancestry():
    ancestor = SimpleNamespace(
        id="ancestor",
        run_type="chat",
        created_by_run_id=None,
        conversation_id=7,
    )
    parent = SimpleNamespace(
        id="parent",
        run_type="resume",
        created_by_run_id="ancestor",
        conversation_id=7,
    )
    current = SimpleNamespace(
        id="current",
        run_type="resume",
        created_by_run_id="parent",
        conversation_id=7,
    )
    repository = ToolMessageAuditRepository(_FakeDb({"parent": parent, "ancestor": ancestor}))

    assert await repository._source_run_ids(current) == ["current", "parent", "ancestor"]


@pytest.mark.asyncio
async def test_resume_source_run_ids_reject_cross_conversation_parent():
    parent = SimpleNamespace(
        id="parent",
        run_type="chat",
        created_by_run_id=None,
        conversation_id=8,
    )
    current = SimpleNamespace(
        id="current",
        run_type="resume",
        created_by_run_id="parent",
        conversation_id=7,
    )
    repository = ToolMessageAuditRepository(_FakeDb({"parent": parent}))

    with pytest.raises(ValueError, match="conversation"):
        await repository._source_run_ids(current)


@pytest.mark.asyncio
async def test_timeline_returns_latest_bounded_items_in_chronological_order():
    """审计时间线只返回最新上限，同时保持前端所需的正序。"""
    newest_first = [SimpleNamespace(id=value) for value in [5, 4, 3, 2]]
    db = _FakeTimelineDb(newest_first)
    repository = ToolMessageAuditRepository(db)

    messages, truncated = await repository.list_timeline_for_conversation(7, limit=3)

    sql = " ".join(str(db.query.compile(compile_kwargs={"literal_binds": True})).split())
    assert (
        "ORDER BY agent_runs.created_at DESC, agent_runs.id DESC, messages.sequence DESC, messages.id DESC LIMIT 4"
    ) in sql
    assert [message.id for message in messages] == [3, 4, 5]
    assert truncated is True
