from __future__ import annotations

from yuxi.modules.agents.runtime.context import BaseContext

from contextlib import aclosing

import pytest

from yuxi.modules.agents.runtime.base import BaseAgent, GraphExecutionResult


class _LifecycleGraph:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle

    async def aget_state(self, config):
        """最终状态只在流耗尽后读取。"""
        self.lifecycle.append("checkpoint")
        return {"messages": []}

    async def astream_events(self, *_args, **_kwargs):
        self.lifecycle.append("stream-created")

        async def events():
            self.lifecycle.append("first-event")
            yield {"method": "values", "params": {"namespace": [], "data": {}}}

        return aclosing(events())


class _CaptureEventsGraph:
    def __init__(self):
        self.last_events_config = None

    async def aget_state(self, config):
        return {"messages": []}

    async def astream_events(self, *_args, **kwargs):
        self.last_events_config = kwargs.get("config")

        async def events():
            yield {"method": "values", "params": {"namespace": [], "data": {}}}

        return aclosing(events())


class _TestAgent(BaseAgent):
    name = "test_agent"
    description = "test"

    async def get_graph(self, **kwargs):
        if getattr(self, "_graph", None) is None:
            self._graph = _CaptureEventsGraph()
        return self._graph


_TestAgent.__module__ = "yuxi.modules.agents.runtime.tests.fake"


async def _collect(agent, *, context=None, **kwargs):
    events = []
    async for event in agent.stream_messages_with_state(
        ["hello"],
        context=context or BaseContext(**{"uid": "user-1", "thread_id": "thread-1"}),
        **kwargs,
    ):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_base_agent_passes_callbacks_metadata_and_tags():
    agent = _TestAgent()

    events = await _collect(
        agent,
        callbacks=["handler-1"],
        metadata={"langfuse_user_id": "user-1"},
        tags=["yuxi"],
    )

    assert events == [
        {"method": "values", "params": {"namespace": [], "data": {}}},
        GraphExecutionResult({"messages": []}),
    ]
    graph = await agent.get_graph()
    assert graph.last_events_config == {
        "configurable": {"thread_id": "thread-1", "uid": "user-1"},
        "recursion_limit": 2000,
        "callbacks": ["handler-1"],
        "metadata": {"langfuse_user_id": "user-1"},
        "tags": ["yuxi"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [42, 2000])
async def test_base_agent_uses_configured_max_execution_steps(limit):
    """显式步数配置覆盖默认值并传入图执行。"""
    agent = _TestAgent()

    await _collect(
        agent,
        context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1", "max_execution_steps": limit}),
    )

    graph = await agent.get_graph()
    assert graph.last_events_config["recursion_limit"] == limit


@pytest.mark.asyncio
async def test_base_agent_records_prepared_after_stream_creation_before_first_event():
    lifecycle = []

    class LifecycleAgent(_TestAgent):
        async def get_graph(self, **kwargs):
            lifecycle.append("graph-ready")
            return _LifecycleGraph(lifecycle)

    async def on_prepared():
        lifecycle.append("prepared")

    agent = LifecycleAgent()
    events = []
    async for event in agent.stream_messages_with_state(
        ["hello"],
        context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1"}),
        on_prepared=on_prepared,
    ):
        events.append(event)

    assert events == [
        {"method": "values", "params": {"namespace": [], "data": {}}},
        GraphExecutionResult({"messages": []}),
    ]
    assert lifecycle == ["graph-ready", "stream-created", "prepared", "first-event", "checkpoint"]


@pytest.mark.asyncio
async def test_base_agent_passes_run_name_to_config():
    """run_name 写入图执行 config，使 tracer 用智能体名命名 trace。"""
    agent = _TestAgent()

    await _collect(agent, run_name="测试智能体")

    graph = await agent.get_graph()
    assert graph.last_events_config["run_name"] == "测试智能体"


@pytest.mark.asyncio
async def test_base_agent_omits_run_name_when_not_given():
    """未传 run_name 时 config 不含该键，trace 名保持 tracer 默认行为。"""
    agent = _TestAgent()

    await _collect(agent)

    graph = await agent.get_graph()
    assert "run_name" not in graph.last_events_config
