from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi import storage_migration
from yuxi.storage_migrations.v071_workdirs import (
    V071ConversationBinding,
    V071WorkdirBinding,
    V071WorkdirMigrationPlan,
)


class _Session:
    def __init__(self, calls: list[object] | None = None):
        self.calls = calls

    async def execute(self, statement):
        if self.calls is not None:
            self.calls.append(("execute", str(statement)))

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_storage_migration_reads_legacy_schema_before_cutover(monkeypatch):
    calls: list[object] = []
    sessions = [_Session(), _Session(), _Session(calls), _Session()]

    @asynccontextmanager
    async def session_context():
        yield sessions.pop(0)

    manager = SimpleNamespace(
        initialize=lambda: calls.append("initialize"),
        create_business_tables=lambda: _record(calls, "create_business_tables"),
        ensure_business_schema=lambda: _record(calls, "ensure_business_schema"),
        get_async_session_context=session_context,
        close=lambda: _record(calls, "close"),
    )
    workdirs = (V071WorkdirBinding("workdir-1", "user-1"),)
    conversations = (V071ConversationBinding("thread-1", "user-1", "workdir-1"),)

    async def read_bindings(_db):
        calls.append("read_v071_workdir_plan")
        return V071WorkdirMigrationPlan(True, workdirs, conversations)

    monkeypatch.setattr(storage_migration, "pg_manager", manager)
    monkeypatch.setattr(storage_migration, "read_v071_workdir_plan", read_bindings)
    monkeypatch.setattr(storage_migration, "_require_quiescence_proof", lambda: calls.append("proof"))
    monkeypatch.setattr(
        storage_migration,
        "_converge_database_state",
        lambda *, fail_nonterminal_runs: _record(calls, f"converge:{fail_nonterminal_runs}"),
    )
    monkeypatch.setattr(
        storage_migration,
        "import_v071_workdirs",
        lambda actual_workdirs, actual_conversations: calls.append(("import", actual_workdirs, actual_conversations)),
    )
    monkeypatch.setattr(storage_migration, "rewrite_v071_workdir_paths", lambda _db: _record(calls, "rewrite"))
    monkeypatch.setattr(storage_migration, "verify_workdir_bindings", lambda _db: _record(calls, "verify"))
    monkeypatch.setattr(
        storage_migration,
        "cleanup_v071_thread_sources",
        lambda actual_conversations: calls.append(("cleanup", actual_conversations)),
    )
    monkeypatch.setattr(storage_migration, "migrate_shared_skills", lambda _db: _record(calls, "skills"))
    monkeypatch.setattr(storage_migration, "mark_v071_skills_migrated", lambda: calls.append("mark_skills"))
    monkeypatch.setattr(
        storage_migration,
        "migrate_runtime_storage_identity",
        lambda: calls.append("runtime_identity"),
    )
    monkeypatch.setattr(storage_migration, "_legacy_skill_roots_exist", lambda: False)
    monkeypatch.setattr(storage_migration, "_legacy_system_config_exists", lambda: False)
    monkeypatch.setattr(storage_migration, "runtime_storage_requires_quiescence", lambda: True)

    await storage_migration.main()

    assert calls.index("read_v071_workdir_plan") < calls.index("ensure_business_schema")
    assert calls.index("proof") < calls.index(("import", workdirs, conversations))
    assert calls.index(("import", workdirs, conversations)) < calls.index("ensure_business_schema")
    assert calls.index("verify") < calls.index(("cleanup", conversations))
    assert calls.index("mark_skills") < calls.index("runtime_identity")
    assert calls[-1] == "close"


@pytest.mark.asyncio
async def test_storage_migration_rejects_v071_schema_without_quiescence_proof(monkeypatch, tmp_path):
    calls: list[str] = []

    @asynccontextmanager
    async def session_context():
        yield _Session()

    manager = SimpleNamespace(
        initialize=lambda: None,
        create_business_tables=lambda: _record(calls, "create"),
        ensure_business_schema=lambda: _record(calls, "schema"),
        get_async_session_context=session_context,
        close=lambda: _record(calls, "close"),
    )
    monkeypatch.setattr(storage_migration, "pg_manager", manager)
    monkeypatch.setattr(
        storage_migration,
        "read_v071_workdir_plan",
        lambda _db: _async_value(V071WorkdirMigrationPlan(True, (), ())),
    )
    monkeypatch.setattr(storage_migration, "_legacy_skill_roots_exist", lambda: False)
    monkeypatch.setattr(storage_migration, "_legacy_system_config_exists", lambda: False)
    monkeypatch.setattr(storage_migration, "runtime_storage_requires_quiescence", lambda: False)
    monkeypatch.setenv("YUXI_STORAGE_MIGRATION_QUIESCENCE_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("YUXI_STORAGE_MIGRATION_QUIESCENCE_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="migrate-storage.sh"):
        await storage_migration.main()

    assert calls == ["close"]


@pytest.mark.asyncio
async def test_current_schema_does_not_rewrite_workdir_data(monkeypatch):
    calls: list[str] = []
    sessions = [_Session(), _Session(), _Session()]

    @asynccontextmanager
    async def session_context():
        yield sessions.pop(0)

    manager = SimpleNamespace(
        initialize=lambda: calls.append("initialize"),
        create_business_tables=lambda: _record(calls, "create"),
        ensure_business_schema=lambda: _record(calls, "schema"),
        get_async_session_context=session_context,
        close=lambda: _record(calls, "close"),
    )
    monkeypatch.setattr(storage_migration, "pg_manager", manager)
    monkeypatch.setattr(
        storage_migration,
        "read_v071_workdir_plan",
        lambda _db: _async_value(V071WorkdirMigrationPlan(False, (), ())),
    )
    monkeypatch.setattr(storage_migration, "_legacy_skill_roots_exist", lambda: False)
    monkeypatch.setattr(storage_migration, "_legacy_system_config_exists", lambda: False)
    monkeypatch.setattr(storage_migration, "runtime_storage_requires_quiescence", lambda: False)
    monkeypatch.setattr(
        storage_migration,
        "_converge_database_state",
        lambda *, fail_nonterminal_runs: _record(calls, f"converge:{fail_nonterminal_runs}"),
    )
    monkeypatch.setattr(storage_migration, "import_v071_workdirs", lambda *_args: calls.append("import"))
    monkeypatch.setattr(storage_migration, "rewrite_v071_workdir_paths", lambda _db: _record(calls, "rewrite"))
    monkeypatch.setattr(storage_migration, "verify_workdir_bindings", lambda _db: _record(calls, "verify"))
    monkeypatch.setattr(storage_migration, "cleanup_v071_thread_sources", lambda *_args: calls.append("cleanup"))
    monkeypatch.setattr(storage_migration, "migrate_shared_skills", lambda _db: _record(calls, "skills"))
    monkeypatch.setattr(storage_migration, "mark_v071_skills_migrated", lambda: calls.append("mark_skills"))
    monkeypatch.setattr(
        storage_migration,
        "migrate_runtime_storage_identity",
        lambda: calls.append("runtime_identity"),
    )

    await storage_migration.main()

    assert "converge:False" in calls
    assert "schema" in calls
    assert "skills" in calls
    assert "runtime_identity" in calls
    assert {"import", "rewrite", "verify", "cleanup"}.isdisjoint(calls)


def test_personal_workspace_skills_never_trigger_shared_skill_migration(monkeypatch, tmp_path):
    personal_skill = tmp_path / "user-data/shared/user-1/workspace/agents/skills/notes"
    personal_skill.mkdir(parents=True)
    monkeypatch.setattr(storage_migration, "get_legacy_storage_dir", lambda: tmp_path / "legacy")
    monkeypatch.setattr(storage_migration, "v071_skill_migration_completed", lambda: False)

    assert storage_migration._legacy_skill_roots_exist() is False


async def _record(calls: list[object], value: str) -> None:
    calls.append(value)


async def _async_value(value):
    return value
