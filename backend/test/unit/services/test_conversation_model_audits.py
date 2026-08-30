from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import conversation_service


@pytest.mark.asyncio
async def test_get_thread_model_audits_view_serializes_explicit_facts(monkeypatch):
    message = SimpleNamespace(
        id=17,
        content="模型输出",
        created_at=datetime(2026, 8, 30, 1, 0, 0),
        run_id="run-1",
        request_id="request-1",
        message_type="model_audit",
        operation_id="operation-1",
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
            "private_internal_field": "must-not-leak",
        },
        tool_calls=[],
    )

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id):
            assert thread_id == "thread-1"
            return SimpleNamespace(id=7, uid="user-1", status="active")

    class FakeModelMessageAuditRepository:
        def __init__(self, _db):
            pass

        async def list_for_conversation(self, conversation_id):
            assert conversation_id == 7
            return [message]

    monkeypatch.setattr(conversation_service, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(conversation_service, "ModelMessageAuditRepository", FakeModelMessageAuditRepository)

    result = await conversation_service.get_thread_model_audits_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
    )

    assert result == {
        "audits": [
            {
                "id": 17,
                "type": "ai",
                "content": "模型输出",
                "created_at": "2026-08-30T01:00:00Z",
                "run_id": "run-1",
                "request_id": "request-1",
                "message_type": "model_audit",
                "operation_id": "operation-1",
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
        ]
    }
    assert "private_internal_field" not in str(result)


@pytest.mark.asyncio
async def test_get_thread_model_audits_view_rejects_other_user(monkeypatch):
    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return SimpleNamespace(id=7, uid="owner", status="active")

    monkeypatch.setattr(conversation_service, "ConversationRepository", FakeConversationRepository)

    with pytest.raises(HTTPException) as exc_info:
        await conversation_service.get_thread_model_audits_view(
            thread_id="thread-1",
            current_uid="other-user",
            db=object(),
        )

    assert exc_info.value.status_code == 404
