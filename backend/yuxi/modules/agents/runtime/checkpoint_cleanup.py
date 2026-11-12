"""使用原图状态 API 清理等待取消，不执行模型或工具。"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START
from langgraph.graph.state import CompiledStateGraph


async def cancel_waitpoint_checkpoint(graph: CompiledStateGraph, config: RunnableConfig) -> None:
    """保留已完成结果，补齐当前工具批次并幂等清空待执行任务。"""
    saved = await graph.aget_state(config)
    pending, completed = _current_tool_batch(saved.values.get("messages") or [])
    if saved.next:
        if pending is None or not pending.tool_calls:
            raise ValueError("等待 checkpoint 缺少未执行的工具调用")
        # 先用 END 合入已完成节点的 pending writes；直接改写消息会丢失业务增量。
        await graph.aupdate_state(config, None, as_node=END)
        saved = await graph.aget_state(config)
        pending, completed = _current_tool_batch(saved.values.get("messages") or [])

    # 首次 END 提交后失联时 next 已为空，仍需为当前批次补齐缺失的工具结果。
    if pending is not None:
        cancelled = [
            ToolMessage(id=f"cancelled:{pending.id}:{call['id']}", tool_call_id=call["id"], content="[已取消]", status="error")
            for call in pending.tool_calls
            if call["id"] not in completed
        ]
        if cancelled:
            await graph.aupdate_state(config, {"messages": cancelled}, as_node=START)

    # START 消息改写会重新生成任务；即使上次在改写后失联，也只清空任务。
    await graph.aupdate_state(config, None, as_node=END)
    saved = await graph.aget_state(config)
    if saved.next or saved.interrupts:
        raise ValueError("等待 checkpoint 清理未收敛")


class CheckpointCleanupModel(BaseChatModel):
    """清理图只能使用状态 API，误调用模型立即失败。"""

    @property
    def _llm_type(self) -> str:
        """标识无外部依赖的 checkpoint 清理模型。"""
        return "checkpoint-cleanup"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """拒绝从维护路径执行模型。"""
        raise RuntimeError("Checkpoint 清理禁止模型执行")


def _current_tool_batch(messages: list[AnyMessage]) -> tuple[AIMessage | None, set[str]]:
    """读取最近 AI 的工具批次，完成结果只属于该 AI 之后的消息。"""
    completed = set()
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message, completed
        if isinstance(message, ToolMessage):
            completed.add(message.tool_call_id)
    return None, completed
