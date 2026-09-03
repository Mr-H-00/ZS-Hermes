from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.services import conversation_service


@pytest.mark.asyncio
async def test_get_thread_message_audits_view_serializes_model_and_tool_facts(monkeypatch):
    model_message = SimpleNamespace(
        id=17,
        role="assistant",
        content="模型输出",
        created_at=datetime(2026, 8, 30, 1, 0, 0),
        run_id="run-1",
        request_id="request-1",
        message_type="model_audit",
        operation_id="model-1",
        started_at=datetime(2026, 8, 30, 1, 0, 1),
        finished_at=datetime(2026, 8, 30, 1, 0, 2),
        duration_ms=875,
        sequence=12,
        execution_status="completed",
        usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        extra_metadata={
            "namespace": ["agent", "model"],
            "model_run_id": "langgraph-model-1",
            "finished_sequence": 18,
            "content": [{"type": "text", "text": "模型输出"}],
            "tool_calls": [],
            "private_internal_field": "must-not-leak",
        },
        tool_calls=[],
    )
    tool_message = SimpleNamespace(
        id=18,
        role="tool",
        content="查询结果",
        created_at=datetime(2026, 8, 30, 1, 0, 2),
        run_id="run-1",
        request_id="request-1",
        message_type="tool_audit",
        operation_id="call-1",
        started_at=datetime(2026, 8, 30, 1, 0, 2),
        finished_at=datetime(2026, 8, 30, 1, 0, 3),
        duration_ms=120,
        sequence=14,
        execution_status="completed",
        usage=None,
        extra_metadata={
            "namespace": [],
            "tool_call_id": "call-1",
            "tool_name": "search",
            "input": {"q": "Yuxi"},
            "output": {"type": "tool", "content": "查询结果", "status": "success"},
            "error_message": None,
            "source_model_operation_id": "model-1",
            "finished_sequence": 15,
            "private_internal_field": "must-not-leak",
        },
        tool_calls=[],
    )

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(id=7, uid="user-1", status="active")

        async def list_message_audits(self, conversation_id, *, limit):
            assert conversation_id == 7
            assert limit == conversation_service.MESSAGE_AUDIT_LIMIT
            return [model_message, tool_message], True

    monkeypatch.setattr(conversation_service, "ConversationRepository", FakeConversationRepository)

    result = await conversation_service.get_thread_message_audits_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
    )

    assert [item["type"] for item in result["audits"]] == ["ai", "tool"]
    assert result["truncated"] is True
    assert result["audits"][0] == {
        "id": 17,
        "type": "ai",
        "content": "模型输出",
        "created_at": "2026-08-30T01:00:00Z",
        "run_id": "run-1",
        "request_id": "request-1",
        "message_type": "model_audit",
        "operation_id": "model-1",
        "started_at": "2026-08-30T01:00:01Z",
        "finished_at": "2026-08-30T01:00:02Z",
        "duration_ms": 875,
        "sequence": 12,
        "finished_sequence": 18,
        "execution_status": "completed",
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        "namespace": ["agent", "model"],
        "model_run_id": "langgraph-model-1",
        "content_blocks": [{"type": "text", "text": "模型输出"}],
        "tool_calls": [],
    }
    tool = result["audits"][1]
    assert tool == {
        "id": 18,
        "type": "tool",
        "content": "查询结果",
        "created_at": "2026-08-30T01:00:02Z",
        "run_id": "run-1",
        "request_id": "request-1",
        "message_type": "tool_audit",
        "operation_id": "call-1",
        "tool_call_id": "call-1",
        "tool_name": "search",
        "tool_input": {"q": "Yuxi"},
        "tool_output": {"type": "tool", "content": "查询结果", "status": "success"},
        "error_message": None,
        "source_model_operation_id": "model-1",
        "started_at": "2026-08-30T01:00:02Z",
        "finished_at": "2026-08-30T01:00:03Z",
        "duration_ms": 120,
        "sequence": 14,
        "finished_sequence": 15,
        "execution_status": "completed",
        "usage": None,
        "namespace": [],
    }
    assert "private_internal_field" not in str(result)
