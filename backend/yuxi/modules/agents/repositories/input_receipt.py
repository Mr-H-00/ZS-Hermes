"""输入接收回执与跨命令幂等键。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.inputs import AgentInputReceipt


class AgentInputReceiptRepository:
    """在调用方事务内保存每次接收的不可变意图。"""

    def __init__(self, db: AsyncSession):
        """绑定调用方持有的事务。"""
        self.db = db

    async def get_for_scope(self, *, uid: str, app_id: str | None, thread_id: str, idempotency_key: str) -> AgentInputReceipt | None:
        """在同一 Thread 的消息与控制命令间共用键空间。"""
        result = await self.db.execute(
            select(AgentInputReceipt).where(
                AgentInputReceipt.uid == uid,
                AgentInputReceipt.app_id == app_id,
                AgentInputReceipt.thread_id == thread_id,
                AgentInputReceipt.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_after_sequence(
        self, *, uid: str, app_id: str | None, thread_id: str, after_sequence: int, limit: int = 100
    ) -> list[AgentInputReceipt]:
        """按持久接收序号读取 Thread 后续事件。"""
        result = await self.db.execute(
            select(AgentInputReceipt)
            .where(
                AgentInputReceipt.uid == uid,
                AgentInputReceipt.app_id == app_id,
                AgentInputReceipt.thread_id == thread_id,
                AgentInputReceipt.receive_seq > after_sequence,
            )
            .order_by(AgentInputReceipt.receive_seq)
            .limit(limit)
        )
        return list(result.scalars())

    async def create(
        self,
        *,
        receipt_id: str,
        idempotency_key: str,
        uid: str,
        app_id: str | None,
        thread_id: str,
        event_type: str,
        intent_hash: str,
        input_id: str | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
    ) -> AgentInputReceipt:
        """保存有独立接收序号的幂等事实。"""
        receipt = AgentInputReceipt(
            id=receipt_id,
            idempotency_key=idempotency_key,
            uid=uid,
            app_id=app_id,
            thread_id=thread_id,
            event_type=event_type,
            intent_hash=intent_hash,
            input_id=input_id,
            turn_id=turn_id,
            run_id=run_id,
        )
        self.db.add(receipt)
        await self.db.flush()
        return receipt
