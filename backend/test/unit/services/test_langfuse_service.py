from __future__ import annotations

from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

import yuxi.modules.agents.services.tracing as svc
import yuxi.infrastructure.observability.langfuse as langfuse


class _FakeLangfuseClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.observations = []
        self.__class__.instances.append(self)

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return f"trace-{seed}"

    def start_observation(self, **kwargs):
        """记录真实 SDK 调用形状，分配稳定测试观察 ID。"""
        observation = _FakeObservation(id=f"{len(self.observations) + 1:016x}", kwargs=kwargs)
        self.observations.append(observation)
        return observation

    def get_trace_url(self, *, trace_id: str | None = None) -> str | None:
        if trace_id is None:
            return None
        return f"https://langfuse.local/trace/{trace_id}"


class _FakeCallbackHandler:
    def __init__(self, *, public_key=None, trace_context=None):
        self.public_key = public_key
        self.trace_context = trace_context
        self.last_trace_id = None


class _FakeObservation:
    def __init__(self, *, id: str, kwargs: dict):
        self.id = id
        self.trace_id = kwargs.get("trace_context", {}).get("trace_id") or "root-trace-1"
        self.kwargs = kwargs
        self.updated = None
        self.ended = False

    def update(self, **kwargs):
        self.updated = kwargs

    def end(self):
        self.ended = True


@pytest.fixture
def run_context_with_last_trace(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.local")
    monkeypatch.setattr(langfuse, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(langfuse, "CallbackHandler", _FakeCallbackHandler)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    langfuse.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        turn_id="turn-1",
        run_id="run-1",
        operation="agent_chat_stream",
    )
    run_context.callbacks[0].last_trace_id = "trace-runtime"
    return run_context


def test_build_run_context_includes_trace_metadata(monkeypatch):
    _FakeLangfuseClient.instances.clear()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.example")
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.setattr(langfuse, "Langfuse", _FakeLangfuseClient)
    monkeypatch.setattr(langfuse, "CallbackHandler", _FakeCallbackHandler)
    monkeypatch.setattr(svc, "CallbackHandler", _FakeCallbackHandler)
    langfuse.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        turn_id="turn-1",
        run_id="run-1",
        operation="agent_chat_stream",
        backend_id="ChatbotAgent",
        message_type="text",
        username="alice",
        login_user_id="alice-login",
        department_id=7,
    )

    assert run_context.trace_id == "trace-turn-1"
    assert len(run_context.callbacks) == 1
    assert run_context.callbacks[0].trace_context == {"trace_id": "trace-turn-1"}
    assert run_context.metadata["langfuse_user_id"] == "user-1"
    assert run_context.metadata["langfuse_session_id"] == "thread-1"
    assert run_context.metadata["backend_id"] == "ChatbotAgent"
    assert run_context.metadata["department_id"] == "7"
    assert run_context.tags == [
        "yuxi",
        "chat",
        "agent_chat_stream",
        "agent:agent-a",
        "message_type:text",
    ]


def test_trace_id_failure_keeps_execution_context_without_callback(run_context_with_last_trace):
    """可选观测初始化失败不能阻止模型执行所需的配置快照。"""
    client = svc.get_langfuse_client()

    def fail_trace_id(*, seed):
        raise RuntimeError(f"trace backend unavailable: {seed}")

    client.create_trace_id = fail_trace_id
    context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        turn_id="turn-2",
        run_id="run-2",
        operation="agent_chat_stream",
    )
    assert context.trace_id is None and context.callbacks == []
    assert context.metadata["run_id"] == "run-2"


def test_callback_failure_closes_new_observation_without_blocking_run(run_context_with_last_trace, monkeypatch):
    """观察已创建但回调失败时须尽力结束观察，并继续无回调执行。"""
    context = run_context_with_last_trace
    root_id = svc.start_turn_observation(context)

    class BrokenCallback:
        def __init__(self, *, trace_context):
            raise RuntimeError(f"callback unavailable: {trace_context}")

    monkeypatch.setattr(svc, "CallbackHandler", BrokenCallback)
    assert svc.attach_run_observation(context, root_observation_id=root_id) is None
    assert context.run_observation is None
    assert svc.get_langfuse_client().observations[-1].ended is True


def test_build_run_context_merges_evaluation_metadata_and_tags(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    langfuse.get_langfuse_client.cache_clear()

    run_context = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        turn_id="turn-1",
        run_id="run-1",
        operation="agent_chat_stream",
        extra_metadata={
            "source": "agent_evaluation",
            "feature": "agent_evaluation",
            "evaluation": {"dataset_name": "agent-eval-smoke"},
        },
        extra_tags=["agent_evaluation", "dataset:agent-eval-smoke", "agent_evaluation"],
    )

    assert run_context.metadata["source"] == "agent_evaluation"
    assert run_context.metadata["feature"] == "agent_evaluation"
    assert run_context.metadata["evaluation"] == {"dataset_name": "agent-eval-smoke"}
    assert run_context.tags == [
        "yuxi",
        "chat",
        "agent_chat_stream",
        "agent:agent-a",
        "agent_evaluation",
        "dataset:agent-eval-smoke",
    ]


def test_get_trace_info_keeps_precreated_trace_id_when_handler_differs(run_context_with_last_trace):
    trace_info = svc.get_trace_info(run_context_with_last_trace)

    assert trace_info == {
        "langfuse_trace_id": "trace-turn-1",
        "langfuse_user_id": "user-1",
        "langfuse_session_id": "thread-1",
    }


def test_turn_root_and_run_observation_share_trace_and_replay_identity(run_context_with_last_trace):
    """后继执行段挂同一 Turn 根观察，重试不制造另一个 Run 观察。"""
    context = run_context_with_last_trace
    client = svc.get_langfuse_client()
    root_id = svc.start_turn_observation(context)
    run_observation_id = svc.attach_run_observation(context, root_observation_id=root_id)

    assert len(root_id) == 16 and root_id == svc.start_turn_observation(context)
    assert run_observation_id == "0000000000000001"
    assert context.trace_id == "trace-turn-1"
    assert client.observations[0].kwargs["trace_context"] == {"trace_id": "trace-turn-1", "parent_span_id": root_id}
    assert context.callbacks[0].trace_context == {"trace_id": "trace-turn-1", "parent_span_id": run_observation_id}
    context.terminal_status = "completed"
    svc.finish_run_observation(context)
    assert client.observations[0].ended is True
    assert client.observations[0].updated["metadata"]["status"] == "completed"

    retry = svc.build_run_context(
        user_id="user-1",
        thread_id="thread-1",
        agent_id="agent-a",
        turn_id="turn-1",
        run_id="run-1",
        operation="agent_chat_stream",
    )
    retry.trace_id = context.trace_id
    assert svc.attach_run_observation(retry, root_observation_id=root_id, existing_observation_id=run_observation_id) == run_observation_id
    assert len(client.observations) == 1


def test_terminal_root_exports_persisted_trace_identity_and_duration(monkeypatch):
    """终态根观察使用已固定的 trace/span ID 和包含等待期的持久时间。"""
    sent = []
    client = SimpleNamespace(api=SimpleNamespace(opentelemetry=SimpleNamespace(export_traces=lambda **kwargs: sent.append(kwargs))))
    monkeypatch.setattr(langfuse, "get_langfuse_client", lambda: client)
    start = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
    end = datetime(2026, 9, 29, 10, 2, tzinfo=UTC)

    langfuse.export_turn_root(
        trace_id="a" * 32,
        root_id="b" * 16,
        turn_id="turn-1",
        thread_id="thread-1",
        uid="user-1",
        status="completed",
        created_at=start,
        finished_at=end,
    )

    span = sent[0]["resource_spans"][0].scope_spans[0].spans[0]
    assert (span.trace_id, span.span_id, span.name) == ("a" * 32, "b" * 16, "agent.turn")
    assert int(span.end_time_unix_nano) - int(span.start_time_unix_nano) == 120_000_000_000
    attributes = {item.key: item.value.string_value for item in span.attributes}
    assert attributes["langfuse.observation.type"] == "agent"
    assert attributes["langfuse.observation.metadata.status"] == "completed"
    assert sent[0]["request_options"]["additional_headers"]["x-langfuse-ingestion-version"] == "4"


async def test_get_trace_url_by_id_async_uses_precreated_trace_id(run_context_with_last_trace):
    trace_url = await langfuse.get_trace_url_by_id_async("trace-turn-1")

    assert trace_url == "https://langfuse.local/trace/trace-turn-1"


async def test_get_trace_url_by_id_async_rejects_non_http_url(run_context_with_last_trace):
    client = svc.get_langfuse_client()
    client.get_trace_url = lambda **_kwargs: "javascript:alert(1)"

    trace_url = await langfuse.get_trace_url_by_id_async("trace-runtime")

    assert trace_url is None


async def test_get_trace_url_by_id_async_rejects_other_https_origin(run_context_with_last_trace):
    client = svc.get_langfuse_client()
    client.get_trace_url = lambda **_kwargs: "https://attacker.example/project/1/traces/trace-runtime"

    trace_url = await langfuse.get_trace_url_by_id_async("trace-runtime")

    assert trace_url is None


async def test_get_trace_url_by_id_async_fails_closed_for_invalid_config(run_context_with_last_trace, monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "not-a-url")

    trace_url = await langfuse.get_trace_url_by_id_async("trace-runtime")

    assert trace_url is None


async def test_get_trace_url_by_id_async_accepts_default_cloud_origin(run_context_with_last_trace, monkeypatch):
    monkeypatch.delenv("LANGFUSE_BASE_URL")
    client = svc.get_langfuse_client()
    client.get_trace_url = lambda **_kwargs: "https://cloud.langfuse.com/project/1/traces/trace-runtime"

    trace_url = await langfuse.get_trace_url_by_id_async("trace-runtime")

    assert trace_url == "https://cloud.langfuse.com/project/1/traces/trace-runtime"
