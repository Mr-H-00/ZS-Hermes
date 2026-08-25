from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

import yuxi.services.artifact_service as svc
from yuxi.agents.backends.paths import workspace_scope_from_runtime_path
from yuxi.workspace.errors import FileTransferLimitError
from yuxi.services.workdir_service import AuthorizedWorkdir
from yuxi.workspace.workdir import Workdir


class _Workspace:
    def __init__(self, skill_root: Path):
        self._write_lock = threading.Lock()
        self.files = {
            "/projects/11111111-1111-4111-8111-111111111111/report.md": b"one\ntwo\n",
            "/notes.txt": b"private",
        }
        self.skill_root = skill_root
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_bytes(b"skill")

    def download_authorized_file_to_path(self, path, target, max_bytes):
        content = self.files.get(path)
        if content is None:
            raise FileNotFoundError(path)
        assert len(content) <= max_bytes
        Path(target).write_bytes(content)
        return len(content)

    def upload_authorized_file_from_path(self, path, source, *, overwrite=True):
        content = Path(source).read_bytes()
        with self._write_lock:
            if not overwrite and path in self.files:
                raise FileExistsError(path)
            self.files[path] = content

    def expected_bytes(self, runtime_path: str) -> bytes:
        if runtime_path.startswith("/home/gem/skills/reporter/"):
            return (self.skill_root / runtime_path.rsplit("/", 1)[-1]).read_bytes()
        return self.files[workspace_scope_from_runtime_path(runtime_path)]

    def add_runtime_file(self, runtime_path: str, content: bytes) -> None:
        self.files[workspace_scope_from_runtime_path(runtime_path)] = content


@pytest.fixture
def live_files(monkeypatch, tmp_path):
    backend = _Workspace(tmp_path / "reporter")
    binding = AuthorizedWorkdir(
        conversation_id=1,
        thread_id="thread-1",
        uid="user-1",
        workdir=Workdir("projects/11111111-1111-4111-8111-111111111111", backend),
        project_id="11111111-1111-4111-8111-111111111111",
        directory_mode="managed",
    )

    async def resolve(**kwargs):
        assert kwargs["uid"] == "user-1"
        return binding

    monkeypatch.setattr(svc, "resolve_authorized_workdir", resolve)
    monkeypatch.setattr(
        svc,
        "UserRepository",
        lambda _db: type(
            "Repo",
            (),
            {"get_by_uid": lambda self, uid: _async_value(type("User", (), {"uid": uid, "is_deleted": False})())},
        )(),
    )
    monkeypatch.setattr(
        svc,
        "list_accessible_skills",
        lambda _db, _user: _async_value([type("Skill", (), {"slug": "reporter", "source_dir": backend.skill_root})()]),
    )
    return backend


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_artifact_allows_project_user_data_and_authorized_skills(live_files):
    for path in (
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        "/home/gem/user-data/notes.txt",
        "/home/gem/skills/reporter/SKILL.md",
    ):
        response = await svc.resolve_thread_artifact_view(
            thread_id="thread-1", current_uid="user-1", db=object(), path=path
        )
        assert Path(response.path).read_bytes() == live_files.expected_bytes(path)
        await response.background()


@pytest.mark.asyncio
async def test_artifact_preview_uses_shared_file_renderer(live_files, monkeypatch):
    path = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.docx"
    live_files.add_runtime_file(path, b"docx bytes")
    captured = {}
    sentinel = {"preview_type": "pdf", "supported": True}

    async def render_preview(file_path, raw_content, *, office_cache_key):
        captured.update(
            path=file_path,
            raw_content=raw_content,
            office_cache_key=office_cache_key,
        )
        return sentinel

    monkeypatch.setattr(svc, "render_file_preview", render_preview)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response is sentinel
    assert captured == {
        "path": path,
        "raw_content": b"docx bytes",
        "office_cache_key": f"artifact:user-1:{path}",
    }


@pytest.mark.asyncio
async def test_artifact_preview_reports_oversized_file_without_rendering(live_files, monkeypatch):
    path = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.docx"

    def reject_large_file(_path, _target, max_bytes):
        assert max_bytes == svc.MAX_BINARY_PREVIEW_SIZE_BYTES
        raise FileTransferLimitError("file exceeds transfer limit")

    async def reject_render(*_args, **_kwargs):
        raise AssertionError("oversized preview must not reach the renderer")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)
    monkeypatch.setattr(svc, "render_file_preview", reject_render)

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        preview=True,
    )

    assert response == svc.preview_too_large().payload()


@pytest.mark.asyncio
@pytest.mark.parametrize("file_name", ["报告.txt", 'quoted"name.txt', "line\nbreak.txt"])
async def test_artifact_download_encodes_untrusted_posix_filename(live_files, file_name):
    path = f"/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/{file_name}"
    live_files.add_runtime_file(path, b"safe")

    response = await svc.resolve_thread_artifact_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path=path,
        download=True,
    )

    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition and "\n" not in disposition
    assert file_name not in disposition
    await response.background()


@pytest.mark.asyncio
async def test_artifact_rejects_other_project(live_files):
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/other/secret.txt",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_artifact_rejects_workdir_viewer_scope(live_files):
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/outputs/report.md",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_artifact_rechecks_current_skill_authorization(live_files, monkeypatch):
    monkeypatch.setattr(svc, "list_accessible_skills", lambda _db, _user: _async_value([]))

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/skills/reporter/SKILL.md",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_artifact_transfer_limit_is_not_reported_as_missing(live_files, monkeypatch):
    def reject_large_file(*_args, **_kwargs):
        raise FileTransferLimitError("file exceeds transfer limit")

    monkeypatch.setattr(live_files, "download_authorized_file_to_path", reject_large_file)
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_thread_artifact_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_save_artifact_copies_live_bytes_to_user_data(live_files):
    result = await svc.save_thread_artifact_to_workspace_view(
        thread_id="thread-1",
        current_uid="user-1",
        db=object(),
        path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
    )
    assert result["saved_path"] == "/home/gem/user-data/saved_artifacts/report.md"
    assert live_files.expected_bytes(result["saved_path"]) == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_concurrent_artifact_saves_use_distinct_atomic_names(live_files):
    second_source = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/outputs/report.md"
    live_files.add_runtime_file(second_source, b"second")

    first, second = await asyncio.gather(
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.md",
        ),
        svc.save_thread_artifact_to_workspace_view(
            thread_id="thread-1",
            current_uid="user-1",
            db=object(),
            path=second_source,
        ),
    )

    assert {first["saved_path"], second["saved_path"]} == {
        "/home/gem/user-data/saved_artifacts/report.md",
        "/home/gem/user-data/saved_artifacts/report (1).md",
    }
    assert set(live_files.expected_bytes(path) for path in (first["saved_path"], second["saved_path"])) == {
        b"one\ntwo\n",
        b"second",
    }
