"""知识投影写入与清理的 PostgreSQL 并发边界。"""

from contextlib import asynccontextmanager

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import pg_manager


async def acquire_knowledge_projection_lock(session, kb_id: str, *, shared: bool = False):
    """在调用方事务内取得知识投影锁。"""
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await session.execute(text(f"SELECT {function}(hashtextextended(:key, 0))"), {"key": f"knowledge-projection:{kb_id}"})


@asynccontextmanager
async def knowledge_projection_lock(kb_id: str, *, shared: bool = False):
    """索引持有共享锁，清理持有排他锁；外部写完成前不能宣布清理完成。"""
    async with pg_manager.get_async_session_context() as session:
        await acquire_knowledge_projection_lock(session, kb_id, shared=shared)
        yield
