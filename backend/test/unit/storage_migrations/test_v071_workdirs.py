from __future__ import annotations

import os
from pathlib import Path

import pytest

import yuxi.storage_migrations.v071_workdirs as svc


def test_import_moves_v071_thread_files_into_user_workspace(monkeypatch, tmp_path: Path):
    previous_umask = os.umask(0o077)
    try:
        legacy_storage = tmp_path / "legacy"
        uploads = legacy_storage / "threads" / "thread-1" / "user-data" / "uploads"
        uploads.mkdir(parents=True)
        (uploads / "input.txt").write_text("input", encoding="utf-8")
        user_data = tmp_path / "user-data"

        monkeypatch.setenv("YUXI_USER_DATA_DIR", str(user_data))
        monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)
        workdirs = (svc.V071WorkdirBinding("11111111-1111-4111-8111-111111111111", "user-1"),)
        conversations = (svc.V071ConversationBinding("thread-1", "user-1", "11111111-1111-4111-8111-111111111111"),)

        svc.import_v071_workdirs(workdirs, conversations)
    finally:
        os.umask(previous_umask)

    target = user_data / "shared" / "user-1" / "workspace" / "projects" / "11111111-1111-4111-8111-111111111111"
    assert (target / "uploads" / "input.txt").read_text(encoding="utf-8") == "input"
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "uploads").stat().st_mode & 0o777 == 0o700
    assert not (target / "outputs").exists()
    assert uploads.is_dir()


def test_import_creates_empty_workdir_without_eager_business_directories(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: tmp_path / "legacy")
    workdirs = (svc.V071WorkdirBinding("22222222-2222-4222-8222-222222222222", "user-1"),)
    conversations = (svc.V071ConversationBinding("thread-empty", "user-1", "22222222-2222-4222-8222-222222222222"),)

    svc.import_v071_workdirs(workdirs, conversations)

    target = tmp_path / "user-data/shared/user-1/workspace/projects/22222222-2222-4222-8222-222222222222"
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700
    assert list(target.iterdir()) == []


def test_import_accepts_v071_thread_id_with_filename_safe_punctuation(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    source = legacy_storage / "threads" / "thread.v0:legacy" / "user-data" / "outputs"
    source.mkdir(parents=True)
    (source / "result.txt").write_text("result", encoding="utf-8")
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)

    svc.import_v071_workdirs(
        (svc.V071WorkdirBinding("33333333-3333-4333-8333-333333333333", "user-1"),),
        (svc.V071ConversationBinding("thread.v0:legacy", "user-1", "33333333-3333-4333-8333-333333333333"),),
    )

    target = (
        tmp_path / "user-data/shared/user-1/workspace/projects/33333333-3333-4333-8333-333333333333/outputs/result.txt"
    )
    assert target.read_text(encoding="utf-8") == "result"


def test_import_does_not_resolve_unsafe_thread_id_outside_threads_root(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    outside = legacy_storage / "escape" / "user-data" / "uploads"
    outside.mkdir(parents=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)

    svc.import_v071_workdirs(
        (svc.V071WorkdirBinding("44444444-4444-4444-8444-444444444444", "user-1"),),
        (svc.V071ConversationBinding("../escape", "user-1", "44444444-4444-4444-8444-444444444444"),),
    )

    target = tmp_path / "user-data/shared/user-1/workspace/projects/44444444-4444-4444-8444-444444444444"
    assert list(target.iterdir()) == []
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_import_does_not_fold_thread_id_onto_another_thread_directory(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    other_thread = legacy_storage / "threads" / "thread" / "user-data" / "uploads"
    other_thread.mkdir(parents=True)
    (other_thread / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)

    svc.import_v071_workdirs(
        (svc.V071WorkdirBinding("55555555-5555-4555-8555-555555555555", "user-1"),),
        (svc.V071ConversationBinding("thread/", "user-1", "55555555-5555-4555-8555-555555555555"),),
    )

    target = tmp_path / "user-data/shared/user-1/workspace/projects/55555555-5555-4555-8555-555555555555"
    assert list(target.iterdir()) == []
    assert (other_thread / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_import_rejects_thread_symlink_without_replacing_existing_target(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    uploads = legacy_storage / "threads" / "thread-1" / "user-data" / "uploads"
    uploads.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (uploads / "escape.txt").symlink_to(outside)
    user_data = tmp_path / "user-data"
    target = user_data / "shared" / "user-1" / "workspace" / "projects" / "11111111-1111-4111-8111-111111111111"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(user_data))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)
    workdirs = (svc.V071WorkdirBinding("11111111-1111-4111-8111-111111111111", "user-1"),)
    conversations = (svc.V071ConversationBinding("thread-1", "user-1", "11111111-1111-4111-8111-111111111111"),)

    with pytest.raises(RuntimeError, match="symlink"):
        svc.import_v071_workdirs(workdirs, conversations)

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert outside.read_text(encoding="utf-8") == "secret"


def test_import_rejects_symlinked_legacy_thread_parent(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    threads = legacy_storage / "threads"
    threads.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "user-data" / "uploads").mkdir(parents=True)
    (outside / "user-data" / "uploads" / "secret.txt").write_text("secret", encoding="utf-8")
    (threads / "thread-1").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)

    with pytest.raises(RuntimeError, match="symlink"):
        svc.import_v071_workdirs(
            (svc.V071WorkdirBinding("safe-target", "user-1"),),
            (svc.V071ConversationBinding("thread-1", "user-1", "safe-target"),),
        )

    assert (outside / "user-data" / "uploads" / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_import_rejects_symlinked_projects_parent(monkeypatch, tmp_path: Path):
    user_data = tmp_path / "user-data"
    workspace = user_data / "shared" / "user-1" / "workspace"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "projects").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(user_data))
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: tmp_path / "legacy")

    with pytest.raises(RuntimeError, match="projects.*symlink"):
        svc.import_v071_workdirs(
            (svc.V071WorkdirBinding("66666666-6666-4666-8666-666666666666", "user-1"),),
            (),
        )

    assert list(outside.iterdir()) == []


def test_import_rejects_unsafe_legacy_identity(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))

    with pytest.raises(RuntimeError, match="不安全"):
        svc.import_v071_workdirs((svc.V071WorkdirBinding("../escape", "user-1"),), ())


def test_rewrite_attachment_keeps_only_current_fields_and_v071_virtual_paths():
    record = {
        "file_id": "file-1",
        "file_name": "report.txt",
        "file_type": "text/plain",
        "file_size": 12,
        "status": "parsed",
        "uploaded_at": "2026-01-01T00:00:00Z",
        "path": "/home/gem/user-data/uploads/attachments/report.md",
        "original_path": "/home/gem/user-data/uploads/report.txt",
        "request_id": "request-1",
        "file_path": "/tmp/report.txt",
        "storage_path": "/app/saves/threads/thread-1/user-data/uploads/report.txt",
        "original_storage_path": "/app/saves/threads/thread-1/user-data/uploads/report.txt",
        "markdown_storage_path": "/app/saves/threads/thread-1/user-data/uploads/attachments/report.md",
        "markdown": "full content",
        "artifact_url": "/api/old",
        "original_artifact_url": "/api/old-original",
        "minio_url": None,
    }

    rewritten = svc._rewrite_attachment(
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
        record,
    )

    assert rewritten == {
        "file_id": "file-1",
        "file_name": "report.txt",
        "file_type": "text/plain",
        "file_size": 12,
        "status": "parsed",
        "uploaded_at": "2026-01-01T00:00:00Z",
        "path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/attachments/report.md",
        "original_path": "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads/report.txt",
        "request_id": "request-1",
    }


def test_cleanup_removes_only_imported_v071_thread_sources(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    source = legacy_storage / "threads" / "thread-1" / "user-data" / "uploads"
    source.mkdir(parents=True)
    (source / "report.txt").write_text("report", encoding="utf-8")
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)
    conversations = (svc.V071ConversationBinding("thread-1", "user-1", "11111111-1111-4111-8111-111111111111"),)

    svc.cleanup_v071_thread_sources(conversations)

    assert not source.exists()


def test_cleanup_v071_thread_sources_surfaces_delete_failure(monkeypatch, tmp_path: Path):
    legacy_storage = tmp_path / "legacy"
    source = legacy_storage / "threads" / "thread-1" / "user-data" / "uploads"
    source.mkdir(parents=True)
    monkeypatch.setattr(svc, "get_legacy_storage_dir", lambda: legacy_storage)
    monkeypatch.setattr(svc.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))

    with pytest.raises(PermissionError, match="denied"):
        svc.cleanup_v071_thread_sources((svc.V071ConversationBinding("thread-1", "user-1", "workdir-1"),))
