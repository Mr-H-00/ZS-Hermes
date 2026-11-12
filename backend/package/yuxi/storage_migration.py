"""在 API、worker 与 Sandbox 启动前完成一次性存储迁移。"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text

from yuxi.config import get_legacy_storage_dir
from yuxi.config.options import ensure_options_in_db
from yuxi.config.runtime import lite_mode_enabled
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.manager import (
    BUSINESS_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    V071_WORKDIR_CUTOVER_STATEMENTS,
    pg_manager,
)
from yuxi.storage_migrations.v071_options import migrate_system_options
from yuxi.storage_migrations.v071_skills import (
    mark_migrated as mark_v071_skills_migrated,
)
from yuxi.storage_migrations.v071_skills import (
    migrate_shared_skills,
)
from yuxi.storage_migrations.v071_skills import (
    migration_completed as v071_skill_migration_completed,
)
from yuxi.storage_migrations.v071_workdirs import (
    cleanup_v071_thread_sources,
    import_v071_workdirs,
    read_v071_workdir_plan,
    rewrite_v071_workdir_paths,
    verify_workdir_bindings,
)
from yuxi.storage_migrations.v072_runtime_identity import (
    migrate_runtime_storage_identity,
    runtime_storage_requires_quiescence,
)
from yuxi.utils import logger

_QUIESCENCE_TOKEN_ENV = "YUXI_STORAGE_MIGRATION_QUIESCENCE_TOKEN"
_QUIESCENCE_FILE_ENV = "YUXI_STORAGE_MIGRATION_QUIESCENCE_FILE"
_MIGRATION_RETRY_LIMIT = 12
_MIGRATION_RETRY_DELAY_SECONDS = 5


class _DatabaseNotReadyError(RuntimeError):
    """表示 PostgreSQL 尚未退出恢复期或仍处于只读状态。"""


def _legacy_skill_roots_exist() -> bool:
    """判断是否仍有会被停机迁移删除的历史共享 Skill 目录。"""
    if v071_skill_migration_completed():
        return False
    shared_skills = get_legacy_storage_dir() / "skills"
    if shared_skills.is_symlink() or (shared_skills.is_dir() and any(shared_skills.iterdir())):
        return True
    return False


def _legacy_system_config_exists() -> bool:
    """判断旧广域目录是否仍保存系统配置。"""
    return (get_legacy_storage_dir() / "config/base.toml").is_file()


def _require_quiescence_proof() -> None:
    """校验由宿主停机脚本创建的一次性证明，禁止普通 up 执行破坏性迁移。"""
    expected = os.getenv(_QUIESCENCE_TOKEN_ENV, "").strip()
    proof_file = Path(
        os.getenv(
            _QUIESCENCE_FILE_ENV,
            str(get_legacy_storage_dir() / ".storage-migration-quiesced"),
        )
    )
    try:
        actual = proof_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("检测到旧文件数据；请先运行 scripts/migrate-storage.sh 停止旧运行环境") from exc
    if not expected or not actual or not hmac.compare_digest(actual, expected):
        raise RuntimeError("存储迁移停机证明无效；请重新运行 scripts/migrate-storage.sh")


async def _converge_database_state(*, fail_nonterminal_runs: bool) -> None:
    """导入历史系统配置；仅在已停机的旧布局切换中收敛非终态 Run。"""
    async with pg_manager.get_async_session_context() as session:
        if fail_nonterminal_runs:
            await AgentRunRepository(session).fail_nonterminal_for_storage_migration()
        await ensure_options_in_db(session)
        await migrate_system_options(
            session,
            legacy_config_file=get_legacy_storage_dir() / "config/base.toml",
        )
        await session.commit()


def _require_supported_version(
    domain: str,
    actual: int | None,
    expected: int,
    *,
    upgrade_from: tuple[int, ...] = (),
) -> None:
    """接受未版本化 baseline、当前版本与显式可升级版本。"""
    if actual not in (None, expected, *upgrade_from):
        raise RuntimeError(f"Unsupported {domain} schema version: {actual}; expected {expected}")


async def _ensure_wps_cloud_schema() -> None:
    """执行 WPS Schema 迁移；迁移能力缺失时拒绝发布版本号。"""
    operation = getattr(pg_manager, "ensure_wps_cloud_schema", None)
    if operation is None:
        raise RuntimeError("WPS cloud schema migration is unavailable")
    await operation()


def _database_startup_error_reason(exc: Exception) -> str | None:
    """识别 PostgreSQL 启动恢复期错误，避免重试真实迁移逻辑错误。"""
    messages = []
    current: BaseException | None = exc
    while current is not None:
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    message = " ".join(messages)
    reasons = (
        ("database system is in recovery", "recovery"),
        ("during recovery", "recovery"),
        ("database system is starting up", "starting_up"),
        ("the database system is starting up", "starting_up"),
        ("could not connect", "connect"),
        ("connection refused", "connect"),
        ("server closed the connection unexpectedly", "connection_closed"),
        ("connection is closed", "connection_closed"),
    )
    for marker, reason in reasons:
        if marker in message:
            return reason
    return None


async def _probe_database_writable() -> None:
    """在执行迁移副作用前确认数据库已完成恢复且允许写入。"""
    engine = getattr(pg_manager, "async_engine", None)
    if engine is None:
        return
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pg_is_in_recovery(), current_setting('transaction_read_only')")
        )
        row = result.one()
    if bool(row[0]):
        raise _DatabaseNotReadyError("the database system is in recovery mode")
    if str(row[1]).lower() == "on":
        raise _DatabaseNotReadyError("database is read-only")


async def _run_migration_once() -> None:
    """执行一轮独占 Schema 迁移；调用方负责处理暂时不可用的数据库。"""
    async with _schema_migration_lock_with_retries():
        async with pg_manager.get_async_session_context() as session:
            workdir_plan = await read_v071_workdir_plan(session)
        migrates_workdirs = workdir_plan.requires_cutover or bool(workdir_plan.workdirs)
        requires_quiescence = (
            migrates_workdirs
            or _legacy_skill_roots_exist()
            or _legacy_system_config_exists()
            or runtime_storage_requires_quiescence()
        )
        if requires_quiescence:
            _require_quiescence_proof()

        await pg_manager.create_schema_version_table()
        versions = await pg_manager.get_schema_versions()
        business_version = versions.get("business")
        _require_supported_version(
            "business",
            business_version,
            BUSINESS_SCHEMA_VERSION,
            upgrade_from=(2, 7, 8),
        )
        knowledge_version = versions.get("knowledge")
        if not lite_mode_enabled():
            _require_supported_version(
                "knowledge",
                knowledge_version,
                KNOWLEDGE_SCHEMA_VERSION,
                upgrade_from=(1, 2, 3),
            )

        if business_version is None:
            await pg_manager.create_business_tables()
        if migrates_workdirs:
            await asyncio.to_thread(import_v071_workdirs, workdir_plan.workdirs, workdir_plan.conversations)
            async with pg_manager.get_async_session_context() as session:
                for statement in V071_WORKDIR_CUTOVER_STATEMENTS:
                    await session.execute(text(statement))
                await rewrite_v071_workdir_paths(session)
                await verify_workdir_bindings(session)
                await session.commit()
        if business_version in {None, 2, 7, 8}:
            if business_version in {None, 2}:
                await pg_manager.ensure_business_schema()
            await _ensure_wps_cloud_schema()
            if business_version is None:
                await pg_manager.setup_langgraph_checkpointer()
            await pg_manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)

        if not lite_mode_enabled() and knowledge_version != KNOWLEDGE_SCHEMA_VERSION:
            if knowledge_version is None:
                await pg_manager.create_knowledge_tables()
            await pg_manager.ensure_knowledge_schema()
            await pg_manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)

        await _converge_database_state(fail_nonterminal_runs=requires_quiescence)
        legacy_config_file = get_legacy_storage_dir() / "config/base.toml"
        if legacy_config_file.is_file() and not legacy_config_file.is_symlink():
            legacy_config_file.unlink()
        if migrates_workdirs:
            await asyncio.to_thread(cleanup_v071_thread_sources, workdir_plan.conversations)
        async with pg_manager.get_async_session_context() as session:
            await migrate_shared_skills(session)
        mark_v071_skills_migrated()
        await asyncio.to_thread(migrate_runtime_storage_identity)


@asynccontextmanager
async def _schema_migration_lock_with_retries():
    """仅在获取迁移锁的恢复期连接失败时重试，避免重放已执行的迁移。"""
    for attempt in range(1, _MIGRATION_RETRY_LIMIT + 1):
        lock_context = pg_manager.schema_migration_lock()
        try:
            await lock_context.__aenter__()
        except Exception as exc:
            reason = _database_startup_error_reason(exc)
            if reason is None or attempt >= _MIGRATION_RETRY_LIMIT:
                raise
            logger.warning(
                "Storage migration database unavailable; retrying "
                "attempt={}/{} delay_seconds={} reason={}",
                attempt,
                _MIGRATION_RETRY_LIMIT,
                _MIGRATION_RETRY_DELAY_SECONDS,
                reason,
            )
            await asyncio.sleep(_MIGRATION_RETRY_DELAY_SECONDS)
            continue
        try:
            await _probe_database_writable()
        except Exception as exc:
            await lock_context.__aexit__(type(exc), exc, exc.__traceback__)
            reason = _database_startup_error_reason(exc)
            if reason is None or attempt >= _MIGRATION_RETRY_LIMIT:
                raise
            logger.warning(
                "Storage migration database unavailable; retrying "
                "attempt={}/{} delay_seconds={} reason={}",
                attempt,
                _MIGRATION_RETRY_LIMIT,
                _MIGRATION_RETRY_DELAY_SECONDS,
                reason,
            )
            await asyncio.sleep(_MIGRATION_RETRY_DELAY_SECONDS)
            continue
        try:
            yield
        except BaseException as exc:
            await lock_context.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            await lock_context.__aexit__(None, None, None)
        return


async def main() -> None:
    """独占迁移数据库 Schema，并在停机窗口切换历史文件 Owner。"""
    pg_manager.initialize()
    try:
        await _run_migration_once()
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
