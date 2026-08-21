from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import workdir_service as svc
from yuxi.workspace.workdir import Workdir


def test_workdir_access_resolves_only_scope_relative_paths():
    workspace = object()
    workdir = Workdir("projects/11111111-1111-4111-8111-111111111111", workspace)
    access = svc.AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=workdir,
    )

    assert access.workdir.workspace is workspace
    assert access.workdir.resolve_path("/") == "/projects/11111111-1111-4111-8111-111111111111"
    assert (
        access.workdir.resolve_path("/outputs/report.md")
        == "/projects/11111111-1111-4111-8111-111111111111/outputs/report.md"
    )
    assert (
        access.workdir.scope_path("/projects/11111111-1111-4111-8111-111111111111/outputs/report.md")
        == "/outputs/report.md"
    )
    for invalid in ("relative.txt", "/../escape", "\\escape"):
        with pytest.raises(ValueError):
            access.workdir.resolve_path(invalid)


@pytest.mark.asyncio
async def test_binding_uses_persisted_relative_workdir(monkeypatch: pytest.MonkeyPatch):
    conversation = SimpleNamespace(
        id=1,
        thread_id="thread-1",
        uid="user-1",
        status="active",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
    )

    class _ConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    opened = {}

    class _Workdir:
        relative_path = "projects/11111111-1111-4111-8111-111111111111"
        root_path = f"/{relative_path}"
        workspace = SimpleNamespace(uid="user-1")

        @classmethod
        def open_existing(cls, uid, workdir_path):
            opened.update(uid=uid, workdir_path=workdir_path)
            return cls()

    monkeypatch.setattr(svc, "ConversationRepository", _ConversationRepository)
    monkeypatch.setattr(svc, "Workdir", _Workdir)

    binding = await svc.resolve_authorized_workdir(thread_id="thread-1", uid="user-1", db=object())

    assert binding.workdir_path == "projects/11111111-1111-4111-8111-111111111111"
    assert binding.workdir.root_path == "/projects/11111111-1111-4111-8111-111111111111"
    assert binding.workdir.workspace.uid == "user-1"
    assert opened == {"uid": "user-1", "workdir_path": "projects/11111111-1111-4111-8111-111111111111"}


@pytest.mark.asyncio
async def test_binding_rejects_cross_user_conversation(monkeypatch: pytest.MonkeyPatch):
    conversation = SimpleNamespace(
        id=1,
        thread_id="thread-1",
        uid="other-user",
        status="active",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
    )

    class _ConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    monkeypatch.setattr(svc, "ConversationRepository", _ConversationRepository)

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_authorized_workdir(thread_id="thread-1", uid="user-1", db=object())

    assert exc.value.status_code == 404
