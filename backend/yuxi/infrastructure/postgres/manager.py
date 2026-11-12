"""PostgreSQL engine、session 和连接生命周期。"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.connection import postgres_conninfo
from yuxi.infrastructure.runtime_settings import get_int_env
from yuxi.shared.singleton import SingletonMeta

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


class PostgresManager(metaclass=SingletonMeta):
    """PostgreSQL engine、业务 session 与 LangGraph checkpoint 连接池。"""

    DATABASE_URL_ENV = "POSTGRES_URL"

    def __init__(self):
        self.async_engine: AsyncEngine | None = None
        self.AsyncSession: async_sessionmaker[AsyncSession] | None = None
        self.langgraph_pool: AsyncConnectionPool | None = None
        self.langgraph_checkpointer: AsyncPostgresSaver | None = None
        self.langgraph_checkpointer_setup = False
        self._initialized = False

    def initialize(self) -> None:
        """初始化数据库连接；配置缺失或创建失败时保持未初始化状态。"""
        if self._initialized:
            return

        db_url = os.getenv(self.DATABASE_URL_ENV)
        if not db_url:
            logger.error(f"环境变量 {self.DATABASE_URL_ENV} 未设置，请在 docker-compose.yml 或 .env 中配置 PostgreSQL 连接字符串")
            return

        try:
            self.async_engine = create_async_engine(
                db_url,
                json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
                json_deserializer=json.loads,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=get_int_env("POSTGRES_POOL_SIZE", 10),
                max_overflow=get_int_env("POSTGRES_MAX_OVERFLOW", 20, minimum=0),
                pool_timeout=get_int_env("POSTGRES_POOL_TIMEOUT_SECONDS", 30),
            )

            self.AsyncSession = async_sessionmaker(
                bind=self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # LangGraph 使用原生 psycopg pool，连接串不能带 SQLAlchemy 方言前缀。
            self.langgraph_pool = AsyncConnectionPool(
                conninfo=postgres_conninfo(db_url),
                max_size=get_int_env("LANGGRAPH_POSTGRES_POOL_SIZE", 10),
                timeout=get_int_env("LANGGRAPH_POSTGRES_POOL_TIMEOUT_SECONDS", 30),
                kwargs={"autocommit": True},  # LangGraph Checkpoint 强依赖 autocommit
                check=AsyncConnectionPool.check_connection,
            )

            self._initialized = True
            logger.info("PostgreSQL manager initialized for knowledge base")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL manager: {e}")

    @asynccontextmanager
    async def schema_migration_lock(self) -> AsyncIterator[None]:
        """为测试与迁移调用方提供统一的 Schema advisory lock。"""
        # migrations 依赖本模块，运行时导入以打破启动期循环。
        from yuxi.migrations.schema import schema_migration_lock

        async with schema_migration_lock(self):
            yield

    @asynccontextmanager
    async def get_async_session_context(self) -> AsyncIterator[AsyncSession]:
        """获取自动提交/回滚并关闭的业务数据库会话。"""
        self.initialize()
        session = self.AsyncSession()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            # HTTP 校验和 SQL 异常可携带密码、授权码与参数，只记录异常类型。
            logger.error(f"PostgreSQL async operation failed: {type(e).__name__}")
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """关闭 engine 与 LangGraph 连接池并重置初始化状态。"""
        if self.async_engine:
            await self.async_engine.dispose()

        if self.langgraph_pool:
            await self.langgraph_pool.close()

        self.async_engine = None
        self.AsyncSession = None
        self.langgraph_pool = None
        self.langgraph_checkpointer = None
        self.langgraph_checkpointer_setup = False
        self._initialized = False

    def require_initialized(self) -> None:
        """要求连接已初始化，供 schema 与 checkpointer 边界显式失败。"""
        if not self._initialized:
            raise RuntimeError("PostgreSQL manager not initialized. Please check configuration.")


pg_manager = PostgresManager()
