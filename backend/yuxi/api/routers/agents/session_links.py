"""Web 产品的消息来源与会话链接，不扩展 Public 协议。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.message_origins import get_input_origins, get_session_navigation
from yuxi.modules.identity.models import User

router = APIRouter(prefix="/agent/threads", tags=["agent"], include_in_schema=False)


async def require_web_scope(request: Request, user: User = Depends(get_required_user)) -> ActorScope:
    """链接读取仅属于当前产品登录用户。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="会话链接仅支持 Web 登录用户")
    return ActorScope(uid=str(user.uid), app_id=None)


@router.get("/{thread_id}/input-origins")
async def input_origins(
    thread_id: str,
    input_ids: list[str] = Query(min_length=1, max_length=100),
    scope: ActorScope = Depends(require_web_scope),
    db: AsyncSession = Depends(get_db),
):
    """按当前消息页的输入身份批量读取来源。"""
    return await get_input_origins(db=db, scope=scope, thread_id=thread_id, input_ids=input_ids)


@router.get("/{thread_id}/navigation")
async def session_navigation(
    thread_id: str,
    scope: ActorScope = Depends(require_web_scope),
    db: AsyncSession = Depends(get_db),
):
    """返回已授权会话的主页面与侧栏目标。"""
    return await get_session_navigation(db=db, scope=scope, thread_id=thread_id)
