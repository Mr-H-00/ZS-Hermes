"""用户共享 Skill 投影的授权快照与目录发布用例。"""

from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.runtime_settings import get_skill_data_dir, get_skill_projection_dir
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.package import (
    compute_skill_directory_hash,
    copy_skill_tree_no_symlinks,
    is_valid_skill_slug,
    validated_shared_skill_parts,
)
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_skill_permission

_USER_SKILLS_LOCK = threading.Lock()
_USER_SKILLS_LOCKS: dict[str, threading.Lock] = {}
_USER_SKILL_PROJECTION_LOCK_SCOPE = "yuxi:skills:user-projection:v1:"


async def refresh_user_skill_projection_async(uid: str) -> dict[str, str]:
    """按数据库中的最新授权快照重建用户共享 Skill 投影。"""
    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.identity.repositories.users import UserRepository

    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("uid is required to refresh the user Skill projection")

    async with pg_manager.get_async_session_context() as db:
        user = await UserRepository().get_by_uid_with_db(db, normalized_uid)
        if user is None or bool(user.is_deleted):
            source_dirs: dict[str, str] = {}
        else:
            source_dirs = {
                item.slug: str(_resolve_shared_skill_dir(item))
                for item in await lock_accessible_shared_skills_for_projection(db, user)
                if item.enabled and item.slug
            }
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
            {"lock_scope": f"{_USER_SKILL_PROJECTION_LOCK_SCOPE}{normalized_uid}"},
        )
        await await_io(asyncio.to_thread(sync_user_accessible_skills, normalized_uid, source_dirs))
        return source_dirs


async def commit_skill_policy_and_refresh_projections(db: AsyncSession, slug: str) -> None:
    """提交 Skill 授权变更，并同步所有已存在的 uid 投影。"""
    uids = await invalidate_skill_projections(db, slug)
    await db.commit()
    await refresh_skill_projections(uids)


async def invalidate_skill_projections(db: AsyncSession, slug: str) -> list[str]:
    """在 owning transaction 提交前撤下已有副本，拒绝沿用旧授权。"""
    from yuxi.modules.workspace.paths import workspace_uid_dirname

    result = await db.execute(select(User.uid).where(User.is_deleted == 0).order_by(User.id))
    projection_root = get_skill_projection_dir()
    uids = [str(uid) for uid in result.scalars().all() if (projection_root / workspace_uid_dirname(str(uid))).is_dir()]
    for uid in uids:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_scope))"),
            {"lock_scope": f"{_USER_SKILL_PROJECTION_LOCK_SCOPE}{uid}"},
        )
    for uid in uids:
        await await_io(asyncio.to_thread(_remove_skill_from_user_projection, uid, slug))
    return uids


async def refresh_skill_projections(uids: list[str]) -> None:
    """提交完成后用新授权重建投影；失败由调用方显式报告。"""
    for uid in uids:
        await refresh_user_skill_projection_async(uid)


async def refresh_committed_skill_projections(uids: list[str]) -> None:
    """提交后刷新派生投影；失败记录日志，执行准备再次读取来源。"""
    try:
        await refresh_skill_projections(uids)
    except Exception:
        logger.exception("Skill 已提交，投影刷新未完成")


def sync_user_accessible_skills(
    uid: str,
    source_dirs: dict[str, str | Path],
) -> Path:
    """将用户有权访问的共享 Skill 来源同步到统一只读目录。"""
    user_skills_root = get_user_skills_root_dir(uid)
    normalized_sources = {
        slug: Path(os.path.abspath(os.fspath(path)))
        for slug, path in source_dirs.items()
        if is_valid_skill_slug(slug) and isinstance(path, (str, Path))
    }
    accessible_slugs = set(normalized_sources)
    with _get_user_skills_lock(uid), _user_skills_file_lock(uid):
        user_skills_root.mkdir(parents=True, exist_ok=True)
        for entry in user_skills_root.iterdir():
            if entry.name in accessible_slugs:
                continue
            _remove_skill_projection_entry(entry)

        for slug, source_dir in normalized_sources.items():
            target_dir = user_skills_root / slug
            temp_target = user_skills_root / f".{slug}.tmp-{uuid.uuid4().hex[:8]}"
            try:
                if skill_dirs_equal(source_dir, target_dir):
                    continue
                copy_skill_tree_no_symlinks(source_dir, temp_target)
                _remove_skill_projection_entry(target_dir)
                temp_target.rename(target_dir)
            except FileNotFoundError:
                logger.warning(f"跳过不存在的 Skill 来源: slug={slug}")
                _remove_skill_projection_entry(target_dir)
            except (OSError, ValueError):
                _remove_skill_projection_entry(target_dir)
                raise
            finally:
                if temp_target.exists():
                    shutil.rmtree(temp_target, ignore_errors=True)

    return user_skills_root


async def lock_accessible_shared_skills_for_projection(db: AsyncSession, user: User) -> list[Skill]:
    """先锁定授权候选，再供投影按最新启停状态复制文件。"""
    repo = SkillRepository(db)
    visible = await repo.list_authorized_for_projection(user)
    locked = {item.id: item for item in await repo.lock_rows_for_read([item.id for item in visible])}
    return [
        locked[item.id]
        for item in visible
        if item.id in locked and resolve_skill_permission(user, locked[item.id]) != ResourcePermission.NONE
    ]


def get_user_skills_root_dir(uid: str) -> Path:
    """计算当前用户共享 Skill 投影根路径，不创建目录。"""
    from yuxi.modules.workspace.paths import workspace_uid_dirname

    safe_uid = workspace_uid_dirname(uid)
    return get_skill_projection_dir() / safe_uid


def skill_dirs_equal(dir1: Path, dir2: Path) -> bool:
    """按 no-follow 字节与执行位比较来源和投影，非法来源显式失败。"""
    source_hash = compute_skill_directory_hash(dir1)
    try:
        return source_hash == compute_skill_directory_hash(dir2)
    except OSError:
        # 缺失或被替换为链接的投影必须重建，不能沿用相同字节的链接。
        return False


def _get_user_skills_lock(uid: str) -> threading.Lock:
    """取得当前进程内按用户划分的投影锁。"""
    with _USER_SKILLS_LOCK:
        lock = _USER_SKILLS_LOCKS.get(uid)
        if lock is None:
            lock = threading.Lock()
            _USER_SKILLS_LOCKS[uid] = lock
        return lock


@contextmanager
def _user_skills_file_lock(uid: str):
    """在共享投影卷上串行化同一用户的目录替换。"""
    from yuxi.modules.workspace.paths import workspace_uid_dirname

    lock_dir = get_skill_projection_dir() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{workspace_uid_dirname(uid)}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _remove_skill_from_user_projection(uid: str, slug: str) -> None:
    """从一个已物化 uid 投影移除 Skill，授权变更时保持 fail-closed。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("无效 skill slug")
    with _get_user_skills_lock(uid), _user_skills_file_lock(uid):
        _remove_skill_projection_entry(get_user_skills_root_dir(uid) / slug)


def _remove_skill_projection_entry(path: Path) -> None:
    """删除一个投影条目，不跟随可能存在的符号链接。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _resolve_shared_skill_dir(item: Skill) -> Path:
    """把已锁定的共享行映射到唯一持久来源。"""
    return get_skill_data_dir().joinpath(*validated_shared_skill_parts(item.slug, item.dir_path))
