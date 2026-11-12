"""Turn 引用所需的最终回答和检索消息查询。"""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.messages import TOOL_AUDIT_MESSAGE_TYPE, Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn


class TurnReferenceRepository:
    """在调用方事务内读取引用事实并保存最终回答的派生信息。"""

    def __init__(self, db: AsyncSession):
        """绑定用例事务。"""
        self.db = db

    async def get_result(self, *, turn_id: str, thread_id: str, uid: str, app_id: str | None):
        """仅返回 completed Turn 明确绑定的顶层最终回答。"""
        result = await self.db.execute(
            select(Message, AgentRun)
            .join(AgentRun, AgentRun.output_message_id == Message.id)
            .join(AgentTurn, AgentTurn.result_run_id == AgentRun.id)
            .where(
                AgentTurn.id == turn_id,
                AgentTurn.thread_id == thread_id,
                AgentTurn.uid == uid,
                AgentTurn.app_id == app_id,
                AgentTurn.status == "completed",
                AgentRun.turn_id == AgentTurn.id,
                Message.turn_id == AgentTurn.id,
                Message.run_id == AgentRun.id,
            )
        )
        return result.one_or_none()

    async def try_lock(self, turn_id: str) -> bool:
        """用事务级锁拒绝同轮重复模型调用，连接关闭时自动释放。"""
        return bool(
            await self.db.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"yuxi:turn-references:{turn_id}"},
            )
        )

    async def list_retrievals(self, turn_id: str) -> list[Message]:
        """汇总本轮各执行段的成功工具结果。"""
        result = await self.db.execute(
            select(Message)
            .where(
                Message.turn_id == turn_id,
                Message.role == "tool",
                Message.message_type == TOOL_AUDIT_MESSAGE_TYPE,
                Message.execution_status == "completed",
            )
            .order_by(Message.id)
        )
        return list(result.scalars())

    async def save(self, message: Message, references: dict) -> None:
        """合并派生标注，保持回答、公开 item 和其他 metadata 原样。"""
        message.extra_metadata = {**(message.extra_metadata or {}), "references": references}
        await self.db.flush()
