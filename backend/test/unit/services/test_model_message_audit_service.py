from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.services import model_message_audit_service
from yuxi.services.model_message_audit_service import ModelMessageAuditCollector


class _FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_collector_projects_real_v3_message_lifecycle(monkeypatch):
    """真实 v3 start/delta/finish 形状必须保留来源键、顺序、时间和 usage。"""
    db = _FakeDb()
    calls: list[tuple[str, dict]] = []

    @asynccontextmanager
    async def session_context():
        yield db

    class FakeRepository:
        def __init__(self, _db):
            pass

        async def start(self, **kwargs):
            calls.append(("start", kwargs))
            return SimpleNamespace(id=1), True

        async def finish(self, **kwargs):
            calls.append(("finish", kwargs))
            return SimpleNamespace(id=1)

    monotonic_values = iter([100.0, 100.321])
    monkeypatch.setattr(model_message_audit_service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(model_message_audit_service, "ModelMessageAuditRepository", FakeRepository)
    monkeypatch.setattr(model_message_audit_service, "monotonic", lambda: next(monotonic_values))

    collector = ModelMessageAuditCollector(
        run_id="run-1",
        request_id="request-1",
        thread_id="thread-1",
        worker_id="worker-1",
    )
    metadata = {
        "run_id": "langchain-model-run-1",
        "stream_event": {
            "method": "messages",
            "namespace": ["model:abc"],
            "seq": 7,
            "timestamp": 1_777_000_123_456,
        },
    }
    await collector.consume(
        {
            "event": "message-start",
            "role": "ai",
            "id": "lc_run--model-message-1",
            "metadata": {"provider": "openai"},
        },
        metadata,
    )
    await collector.consume(
        {
            "event": "content-block-delta",
            "index": 0,
            "delta": {"type": "text-delta", "text": "hello"},
        },
        metadata,
    )
    await collector.consume(
        {
            "event": "content-block-finish",
            "index": 0,
            "content": {"type": "text", "text": "hello"},
        },
        metadata,
    )
    finish_metadata = {
        **metadata,
        "stream_event": {**metadata["stream_event"], "seq": 8, "timestamp": 1_777_000_123_789},
    }
    await collector.consume(
        {
            "event": "message-finish",
            "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            "metadata": {"model_name": "deterministic-chat"},
        },
        finish_metadata,
    )

    assert db.commits == 2
    assert calls[0][0] == "start"
    assert calls[0][1]["operation_id"] == "lc_run--model-message-1"
    assert calls[0][1]["sequence"] == 7
    assert calls[0][1]["metadata"]["model_run_id"] == "langchain-model-run-1"
    assert calls[1] == (
        "finish",
        {
            "run_id": "run-1",
            "request_id": "request-1",
            "thread_id": "thread-1",
            "worker_id": "worker-1",
            "operation_id": "lc_run--model-message-1",
            "content": "hello",
            "finished_at": calls[1][1]["finished_at"],
            "duration_ms": 321,
            "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            "metadata": {
                "namespace": ["model:abc"],
                "content": [{"type": "text", "text": "hello"}],
                "tool_calls": [],
                "finished_sequence": 8,
                "finish_metadata": {"model_name": "deterministic-chat"},
            },
        },
    )


@pytest.mark.asyncio
async def test_collector_rejects_start_without_protocol_sequence():
    collector = ModelMessageAuditCollector(
        run_id="run-1",
        request_id="request-1",
        thread_id="thread-1",
        worker_id="worker-1",
    )

    with pytest.raises(ValueError, match="seq"):
        await collector.consume(
            {"event": "message-start", "role": "ai", "id": "message-1"},
            {"run_id": "model-run-1", "stream_event": {"timestamp": 1_777_000_123_456}},
        )
