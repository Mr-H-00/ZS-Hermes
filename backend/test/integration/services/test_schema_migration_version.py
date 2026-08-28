"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, PostgresManager
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试没有 HTTP 资源需要清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 测试没有 Sandbox 资源需要清理。"""
    yield


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰进程单例的隔离 manager。"""
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def test_schema_migration_lock_serializes_real_postgres_sessions() -> None:
    """两个 migrator 竞争同一 advisory lock 时只允许一个进入临界区。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    manager = _scoped_manager(engine)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_migrator() -> None:
        async with manager.schema_migration_lock():
            first_entered.set()
            await release_first.wait()

    async def second_migrator() -> None:
        await first_entered.wait()
        async with manager.schema_migration_lock():
            second_entered.set()

    first_task = asyncio.create_task(first_migrator())
    second_task = asyncio.create_task(second_migrator())
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert second_entered.is_set() is False
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        assert second_entered.is_set() is True
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        await engine.dispose()


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema = f"pytest_schema_version_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        manager = _scoped_manager(scoped_engine)

        with pytest.raises(RuntimeError, match="business=missing"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match=f"business={BUSINESS_SCHEMA_VERSION + 1}"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
        await manager.require_current_schema(include_knowledge=False)
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_business_v1_upgrades_to_scheduled_schema_idempotently() -> None:
    """隔离 v1 数据库升级两次后保留数据并建立完整调度约束。"""
    schema = f"pytest_scheduled_migration_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None
    uid = f"migration-user-{uuid.uuid4().hex[:12]}"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    run_id = f"scheduled-run-{uuid.uuid4()}"

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped_engine = create_async_engine(
            os.environ["POSTGRES_URL"],
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with scoped_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("DROP TABLE scheduled_agent_runs"))
            await connection.execute(text("DROP TABLE scheduled_agent_jobs"))
            await connection.execute(
                text(
                    """
                    INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted)
                    VALUES ('migration user', :uid, '$argon2id$placeholder', 'user', 0, 0)
                    """
                ),
                {"uid": uid},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO projects (
                        id, uid, name, selection_status, workdir_path, directory_mode
                    ) VALUES (
                        :project_id, :uid, 'Migration Project', 'selectable',
                        :workdir_path, 'managed'
                    )
                    """
                ),
                {"project_id": project_id, "uid": uid, "workdir_path": f"projects/{project_id}"},
            )

        manager = _scoped_manager(scoped_engine)
        await manager.create_schema_version_table()
        await manager.record_schema_version("business", 1)
        await manager.ensure_business_schema()

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO scheduled_agent_jobs (
                        id, uid, creation_request_id, creation_intent_hash,
                        project_id, agent_slug, name, prompt, tool_approval_mode,
                        cron_expression, timezone, enabled, next_run_at
                    ) VALUES (
                        :job_id, :uid, :creation_request_id, :creation_intent_hash,
                        :project_id, 'chatbot', 'Preserved Job', 'hello', 'default',
                        '0 9 * * *', 'UTC', TRUE, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "uid": uid,
                    "creation_request_id": f"migration-create-{uuid.uuid4()}",
                    "creation_intent_hash": "0" * 64,
                    "project_id": project_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO scheduled_agent_runs (
                        id, job_id, request_id, thread_id, trigger, occurrence_key,
                        scheduled_for, project_id, agent_slug, conversation_title,
                        prompt, tool_approval_mode, status
                    ) VALUES (
                        :run_id, :job_id, :request_id, :thread_id, 'scheduled', 'scheduled:v1',
                        CURRENT_TIMESTAMP, :project_id, 'chatbot', 'Preserved Job',
                        'hello', 'default', 'dispatching'
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "job_id": job_id,
                    "request_id": f"request-{uuid.uuid4()}",
                    "thread_id": f"thread-{uuid.uuid4()}",
                    "project_id": project_id,
                },
            )

        await manager.ensure_business_schema()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)

        async with scoped_engine.connect() as connection:
            tables = {
                row.table_name
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = :schema
                              AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            columns = {
                (row.table_name, row.column_name)
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT table_name, column_name
                            FROM information_schema.columns
                            WHERE table_schema = :schema
                              AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            constraints = {
                row.conname: row.definition
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT con.conname, pg_get_constraintdef(con.oid) AS definition
                            FROM pg_constraint AS con
                            JOIN pg_namespace AS ns ON ns.oid = con.connamespace
                            WHERE ns.nspname = :schema
                              AND con.conname IN (
                                  'fk_scheduled_agent_jobs_project_uid',
                                  'uq_scheduled_agent_jobs_uid_creation_request',
                                  'scheduled_agent_runs_job_id_fkey',
                                  'uq_scheduled_agent_runs_job_occurrence',
                                  'uq_scheduled_agent_runs_request',
                                  'uq_scheduled_agent_runs_thread'
                              )
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            indexes = {
                row.indexname
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT indexname
                            FROM pg_indexes
                            WHERE schemaname = :schema
                              AND tablename IN ('scheduled_agent_jobs', 'scheduled_agent_runs')
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            preserved = await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM users WHERE uid = :uid) AS users,
                        (SELECT count(*) FROM projects WHERE id = :project_id) AS projects,
                        (SELECT count(*) FROM scheduled_agent_jobs WHERE id = :job_id) AS jobs,
                        (SELECT count(*) FROM scheduled_agent_runs WHERE id = :run_id) AS runs
                    """
                ),
                {"uid": uid, "project_id": project_id, "job_id": job_id, "run_id": run_id},
            )
            counts = preserved.one()

        assert tables == {"scheduled_agent_jobs", "scheduled_agent_runs"}
        assert {
            ("scheduled_agent_jobs", "model_spec"),
            ("scheduled_agent_jobs", "creation_request_id"),
            ("scheduled_agent_jobs", "creation_intent_hash"),
            ("scheduled_agent_runs", "model_spec"),
        }.issubset(columns)
        assert "ON DELETE CASCADE" in constraints["fk_scheduled_agent_jobs_project_uid"]
        assert "ON DELETE CASCADE" in constraints["scheduled_agent_runs_job_id_fkey"]
        assert "UNIQUE (uid, creation_request_id)" in constraints["uq_scheduled_agent_jobs_uid_creation_request"]
        assert {
            "uq_scheduled_agent_runs_job_occurrence",
            "uq_scheduled_agent_runs_request",
            "uq_scheduled_agent_runs_thread",
        }.issubset(constraints)
        assert {
            "ix_scheduled_agent_jobs_due",
            "ix_scheduled_agent_runs_job_created",
            "ix_scheduled_agent_runs_dispatching",
        }.issubset(indexes)
        assert tuple(counts) == (1, 1, 1, 1)
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
