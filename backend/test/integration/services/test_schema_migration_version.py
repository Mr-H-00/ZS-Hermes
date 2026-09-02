"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION, PostgresManager
from yuxi.storage.postgres.models_business import Base

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

LEGACY_TASK_TABLE_SQL = """
CREATE TABLE tasks (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress DOUBLE PRECISION NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    payload JSONB,
    result JSONB,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE
)
"""


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


async def _create_isolated_manager(prefix: str):
    """创建位于独立 PostgreSQL Schema 的 manager 与清理句柄。"""
    schema = f"{prefix}_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    return schema, admin_engine, scoped_engine, _scoped_manager(scoped_engine)


async def _drop_isolated_schema(schema: str, admin_engine, scoped_engine) -> None:
    """释放隔离 Schema 及其 engine。"""
    await scoped_engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


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


async def test_business_schema_converges_both_v2_branches_idempotently() -> None:
    """合并后的收敛同时补齐 Durable Task 与 Project 生命周期结构。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_schema")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE projects DROP CONSTRAINT ck_projects_status"))
            await connection.execute(text("ALTER TABLE projects DROP COLUMN deleted_at"))
            await connection.execute(text("ALTER TABLE projects DROP COLUMN status"))
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-running', 'legacy', 'knowledge_parse', 'running')"
                )
            )

        await manager.ensure_business_schema()
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            task_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'tasks'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            row = (
                await connection.execute(
                    text("SELECT status, error, handler_version, attempt_count FROM tasks WHERE id = 'legacy-running'")
                )
            ).one()
            project_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'projects'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            project_constraints = set(
                (
                    await connection.execute(
                        text(
                            "SELECT constraint_name FROM information_schema.table_constraints "
                            "WHERE table_schema = :schema AND table_name = 'projects'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )

        assert {
            "handler_version",
            "dedupe_key",
            "attempt_count",
            "worker_id",
            "heartbeat_at",
            "lease_expires_at",
            "timeout_seconds",
        } <= task_columns
        assert tuple(row) == ("running", None, 0, 0)
        assert {"status", "deleted_at"} <= project_columns
        assert "ck_projects_status" in project_constraints
        assert BUSINESS_SCHEMA_VERSION == 4
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_v1_to_v2_adds_file_attempt_owner_idempotently() -> None:
    """知识 schema 相邻升级为文件中间态增加 Task attempt fencing。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_schema")
    try:
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE knowledge_files ("
                    "id SERIAL PRIMARY KEY, file_id VARCHAR(64) NOT NULL, status VARCHAR(32), "
                    "error_message TEXT, updated_at TIMESTAMPTZ)"
                )
            )
            await connection.execute(
                text("INSERT INTO knowledge_files (file_id, status) VALUES ('legacy-file', 'parsing')")
            )

        await manager.upgrade_knowledge_schema_v1_to_v2()
        await manager.upgrade_knowledge_schema_v1_to_v2()

        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'knowledge_files'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            legacy = (
                await connection.execute(
                    text("SELECT status, error_message FROM knowledge_files WHERE file_id = 'legacy-file'")
                )
            ).one()
        assert {"processing_task_id", "processing_owner"} <= columns
        assert tuple(legacy) == (
            "error_parsing",
            "service_interrupted: 旧执行实例中断，处理结果未知，请重试",
        )
        assert KNOWLEDGE_SCHEMA_VERSION == 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_knowledge_baseline_adds_timestamp_before_owner_convergence() -> None:
    """未版本化的旧表缺少 updated_at 时仍能收敛中间态。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_baseline")
    try:
        await manager.create_knowledge_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('legacy-kb', 'legacy', 'milvus')")
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files (file_id, kb_id, filename, status) "
                    "VALUES ('legacy-file', 'legacy-kb', 'legacy.txt', 'indexing')"
                )
            )
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_task_id"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_owner"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN updated_at"))

        await manager.create_knowledge_tables()
        await manager.ensure_knowledge_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error_message, updated_at, processing_task_id, processing_owner "
                        "FROM knowledge_files WHERE file_id = 'legacy-file'"
                    )
                )
            ).one()
        assert row.status == "error_indexing"
        assert row.error_message == "service_interrupted: 旧执行实例中断，处理结果未知，请重试"
        assert row.updated_at is not None
        assert row.processing_task_id is None
        assert row.processing_owner is None
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_baseline_repairs_existing_legacy_task_table() -> None:
    """未版本化数据库的 create_all + ensure 路径必须补齐旧 tasks 表。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_baseline")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-pending', 'legacy', 'knowledge_parse', 'pending')"
                )
            )

        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error, handler_version, lease_expires_at FROM tasks WHERE id = 'legacy-pending'"
                    )
                )
            ).one()
        assert tuple(row) == ("pending", None, 0, None)
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_schema_version")

    try:
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
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_develop_v3_business_schema_upgrades_to_scheduled_schema_idempotently() -> None:
    """develop v3 数据库重复升级后保留原数据并建立完整调度约束。"""
    schema = f"pytest_scheduled_migration_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    scoped_engine = None
    uid = f"migration-user-{uuid.uuid4().hex[:12]}"
    project_id = str(uuid.uuid4())

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
        await manager.record_schema_version("business", 3)
        await manager.ensure_business_schema()
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
                        (SELECT count(*) FROM projects WHERE id = :project_id) AS projects
                    """
                ),
                {"uid": uid, "project_id": project_id},
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
        assert tuple(counts) == (1, 1)
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
    finally:
        if scoped_engine is not None:
            await scoped_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
