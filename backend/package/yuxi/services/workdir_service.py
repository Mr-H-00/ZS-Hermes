"""授权 Conversation 对持久化 Project Workdir 的访问。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.workspace.workdir import Workdir


@dataclass(frozen=True, slots=True)
class AuthorizedWorkdir:
    """Service 授权上下文与持久化 Workdir。"""

    conversation_id: int
    thread_id: str
    uid: str
    workdir: Workdir

    @property
    def workdir_path(self) -> str:
        return self.workdir.relative_path


async def resolve_authorized_workdir(*, thread_id: str, uid: str, db) -> AuthorizedWorkdir:
    """授权线程并打开其持久化 Workdir。"""
    conversation = await ConversationRepository(db).get_conversation_by_thread_id(thread_id)
    if conversation is None or conversation.uid != str(uid) or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")
    if not conversation.workdir_path:
        raise RuntimeError("Conversation 缺少 Workdir 路径")
    return AuthorizedWorkdir(
        conversation_id=conversation.id,
        thread_id=conversation.thread_id,
        uid=str(uid),
        workdir=Workdir.open_existing(str(uid), conversation.workdir_path),
    )
