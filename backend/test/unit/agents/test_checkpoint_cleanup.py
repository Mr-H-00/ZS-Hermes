"""等待取消的 checkpoint 重入单测。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from yuxi.modules.agents.runtime.checkpoint_cleanup import cancel_waitpoint_checkpoint

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class FakeCheckpointGraph:
    """模拟首个持久改写成功后进程失联。"""

    def __init__(self, message: AIMessage):
        """保存初始消息与待执行节点。"""
        self.message = message
        self.messages = [message]
        self.next = ("tools",)
        self.updates = []
        self.crash_after_patch = False

    async def aget_state(self, _config):
        """返回当前持久状态。"""
        return SimpleNamespace(next=self.next, interrupts=(), values={"messages": self.messages})

    async def aupdate_state(self, _config, values, *, as_node):
        """持久改写后可模拟崩溃，空改写推进节点。"""
        self.updates.append((values, as_node))
        if values and values.get("messages"):
            self.messages.extend(values["messages"])
            if self.crash_after_patch:
                raise RuntimeError("进程失联")
        else:
            self.next = ()


async def test_waitpoint_cleanup_retries_after_message_patch():
    """首次改写成功但未推进节点时，重试仍能收敛。"""
    graph = FakeCheckpointGraph(AIMessage(id="pending", content="", tool_calls=[{"id": "tool-1", "name": "act", "args": {}}]))
    graph.crash_after_patch = True
    with pytest.raises(RuntimeError, match="进程失联"):
        await cancel_waitpoint_checkpoint(graph, {})

    assert graph.messages[-1].content == "[已取消]"
    assert graph.messages[-1].tool_call_id == "tool-1"
    assert graph.next == ()

    graph.crash_after_patch = False
    await cancel_waitpoint_checkpoint(graph, {})

    assert graph.next == ()
    assert len(graph.updates) == 3
    assert graph.updates[-1] == (None, "__end__")


async def test_waitpoint_cleanup_rejects_unrelated_checkpoint_without_tool_calls():
    """末尾普通 AI 消息不能伪装成已经清理过的等待点。"""
    graph = FakeCheckpointGraph(AIMessage(id="ordinary", content="普通回答", tool_calls=[]))

    with pytest.raises(ValueError, match="缺少未执行的工具调用"):
        await cancel_waitpoint_checkpoint(graph, {})

    assert graph.updates == []


async def test_parallel_waitpoint_keeps_completed_tool_result():
    """末条 ToolMessage 和多个待办节点均不能阻止清理。"""
    graph = FakeCheckpointGraph(
        AIMessage(
            id="pending",
            content="",
            tool_calls=[
                {"id": "finished", "name": "read", "args": {}},
                {"id": "waiting", "name": "ask", "args": {}},
            ],
        )
    )
    completed = ToolMessage(tool_call_id="finished", content="已有结果")
    graph.messages.append(completed)
    graph.next = ("tools", "another")

    await cancel_waitpoint_checkpoint(graph, {})
    await cancel_waitpoint_checkpoint(graph, {})

    assert graph.next == ()
    assert graph.messages[1] is completed
    assert [message.tool_call_id for message in graph.messages if isinstance(message, ToolMessage)] == [
        "finished",
        "waiting",
    ]


async def test_reused_tool_call_id_does_not_consume_or_replace_an_old_result():
    """模型复用 call id 时，完成检测与取消消息身份仍属于当前 AI。"""
    call = {"id": "reused", "name": "ask", "args": {}}
    pending = AIMessage(id="current", content="", tool_calls=[call])
    graph = FakeCheckpointGraph(pending)
    old_result = ToolMessage(id="cancelled:previous:reused", tool_call_id="reused", content="旧结果")
    graph.messages = [AIMessage(id="previous", content="", tool_calls=[call]), old_result, pending]

    await cancel_waitpoint_checkpoint(graph, {})

    assert graph.messages[1] is old_result
    assert graph.messages[-1].tool_call_id == "reused"
    assert graph.messages[-1].content == "[已取消]"
    assert graph.messages[-1].id != old_result.id


@pytest.mark.parametrize("crash_after", [None, "end", "messages"])
async def test_parallel_graph_preserves_pending_writes_and_recovers_each_commit(monkeypatch, crash_after):
    """真实 reducer 保留 sibling 结果与业务增量，任一提交后重入不执行工具。"""
    import operator
    from typing import Annotated, TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.types import interrupt

    class State(TypedDict):
        """消息与非幂等业务增量均由原图 reducer 拥有。"""

        messages: Annotated[list, add_messages]
        finished_steps: Annotated[list[str], operator.add]

    calls = []

    def finished(state):
        """已完成 sibling 的结果尚在 pending writes。"""
        calls.append("finished")
        return {
            "messages": [ToolMessage(id="done", tool_call_id="finished", content="已有结果")],
            "finished_steps": ["once"],
        }

    def waiting(state):
        """取消不能重新执行这个等待工具。"""
        calls.append("waiting")
        interrupt("answer required")
        raise AssertionError("取消执行了等待工具")

    builder = StateGraph(State)
    builder.add_node("finished", finished)
    builder.add_node("waiting", waiting)
    builder.add_edge(START, "finished")
    builder.add_edge(START, "waiting")
    builder.add_edge("finished", END)
    builder.add_edge("waiting", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "parallel"}}
    await graph.ainvoke(
        {
            "messages": [
                AIMessage(
                    id="pending",
                    content="",
                    tool_calls=[
                        {"id": "finished", "name": "read", "args": {}},
                        {"id": "waiting", "name": "ask", "args": {}},
                    ],
                )
            ]
        },
        config,
    )
    executed = list(calls)
    update_state = graph.aupdate_state

    async def crash_once(config, values, *, as_node):
        """只在持久提交成功后注入一次进程失联。"""
        nonlocal crash_after
        result = await update_state(config, values, as_node=as_node)
        if (crash_after == "end" and as_node == END) or (crash_after == "messages" and values):
            crash_after = None
            raise RuntimeError("提交后失联")
        return result

    monkeypatch.setattr(graph, "aupdate_state", crash_once)
    if crash_after:
        with pytest.raises(RuntimeError, match="提交后失联"):
            await cancel_waitpoint_checkpoint(graph, config)
    await cancel_waitpoint_checkpoint(graph, config)
    await cancel_waitpoint_checkpoint(graph, config)
    saved = await graph.aget_state(config)
    assert saved.next == () and saved.interrupts == ()
    assert saved.values["finished_steps"] == ["once"]
    assert [(message.tool_call_id, message.content) for message in saved.values["messages"] if isinstance(message, ToolMessage)] == [
        ("finished", "已有结果"),
        ("waiting", "[已取消]"),
    ]
    assert calls == executed
