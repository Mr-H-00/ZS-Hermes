# ruff: noqa: E402  # 入口必须先加载环境。
"""部署时统一初始化 Schema 与代码拥有的共享持久内容。"""

import asyncio

from zs.bootstrap.environment import load_environment

load_environment()

from zs.bootstrap.options import ensure_options_in_db
from zs.infrastructure.observability.logging import logger
from zs.infrastructure.postgres.manager import pg_manager
from zs.infrastructure.postgres.models import load_models
from zs.migrations.main import initialize_schema
from zs.migrations.schema import schema_migration_lock
from zs.modules.agents.runtime.sandbox import validate_sandbox_configuration
from zs.modules.agents.services.configuration import initialize_agent_presets
from zs.modules.extensions.mcp.service import ensure_builtin_mcp_servers_in_db
from zs.modules.extensions.skills.builtin.sync import init_builtin_skills
from zs.modules.models.providers.service import ensure_builtin_model_providers_in_db
from zs.modules.system.services.options import invalidate_option_cache


async def main() -> None:
    """串行准备共享内容；部分阶段失败保留已提交数据，修正后幂等重跑。"""
    validate_sandbox_configuration()
    load_models()
    pg_manager.initialize()
    try:
        async with schema_migration_lock(pg_manager):
            logger.info("app-init: schema")
            await initialize_schema(pg_manager)
            logger.info("app-init: options and model providers")
            async with pg_manager.get_async_session_context() as session:
                mineru_migrated = await ensure_options_in_db(session)
                await ensure_builtin_model_providers_in_db(session)
            if mineru_migrated:
                await invalidate_option_cache("mineru_ocr_host_opts")
            logger.info("app-init: agent presets")
            async with pg_manager.get_async_session_context() as session:
                await initialize_agent_presets(session)
            logger.info("app-init: builtin MCP definitions")
            await ensure_builtin_mcp_servers_in_db()
            logger.info("app-init: builtin Skills")
            async with pg_manager.get_async_session_context() as session:
                await init_builtin_skills(session)
            logger.info("app-init: complete")
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
