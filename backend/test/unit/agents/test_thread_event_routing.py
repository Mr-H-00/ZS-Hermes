from contextlib import aclosing

import pytest
from langchain_core.messages import AIMessageChunk

from yuxi.modules.agents.runtime.base import BaseAgent
from yuxi.modules.agents.runtime.context import BaseContext


@pytest.mark.asyncio
@pytest.mark.parametrize("container", ["configurable", "metadata", "stream_event", "meta"])
async def test_langgraph_message_does_not_route_by_nested_thread_id(container):
    """消息归属只能来自协议 metadata 的顶层字段。"""

    class Graph:
        """提供外部流的明确 namespace 与嵌套业务数据。"""

        async def astream_events(self, *_args, **_kwargs):
            """同时提供未路由和已明确路由的子线程消息。"""

            async def events():
                """嵌套字段不能将未路由消息变成父线程消息。"""
                for metadata in ({container: {"thread_id": "parent-thread"}}, {"thread_id": "child-thread"}):
                    yield {
                        "method": "messages",
                        "params": {
                            "namespace": ["child:task"],
                            "data": (AIMessageChunk(content="child"), metadata),
                        },
                    }

            return aclosing(events())

        async def aget_state(self, _config):
            """提供流终点的状态。"""
            return {}

    class Agent(BaseAgent):
        """使用真实接入转换逻辑。"""

        async def get_graph(self, **_kwargs):
            """返回测试协议流。"""
            return Graph()

    events = [
        event async for event in Agent().stream_messages_with_state(["hello"], context=BaseContext(thread_id="parent-thread", uid="user-1"))
    ]
    assert "thread_id" not in events[0]["params"]["data"][1]
    assert events[1]["params"]["data"][1]["thread_id"] == "child-thread"
