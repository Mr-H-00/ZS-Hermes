"""Web 消息的可信来源与会话导航信息。"""

from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.thread_access import require_thread


def navigation_target(member) -> dict:
    """来源身份与根页面分别表达，成员在根页面侧栏打开。"""
    return {
        "session_id": member.thread_id,
        "tree_root_session_id": member.tree_root_thread_id,
        "parent_session_id": member.parent_thread_id,
        "name": member.title or member.cooperation_name,
    }


async def get_session_navigation(*, db, scope: ActorScope, thread_id: str) -> dict:
    """按完整用户与 APP 作用域解析目标页面。"""
    member = await require_thread(db=db, scope=scope, thread_id=thread_id)
    return navigation_target(member)


async def get_input_origins(*, db, scope: ActorScope, thread_id: str, input_ids: list[str]) -> dict:
    """从持久 Input 批量读取非人工来源，人工输入没有来源标记。"""
    await require_thread(db=db, scope=scope, thread_id=thread_id)
    rows = await AgentInputRepository(db).origins(thread_id=thread_id, uid=scope.uid, app_id=scope.app_id, input_ids=input_ids)
    origins = {}
    for input_id, source, api_key_id, source_run_id, member in rows:
        if source == "cooperation":
            origins[input_id] = {
                "kind": "session",
                "label": navigation_target(member)["name"] if member else "协作会话",
                "target": {**navigation_target(member), "run_id": source_run_id} if member else None,
            }
        elif source == "scheduled_agent":
            origins[input_id] = {"kind": "schedule", "label": "定时任务", "target": None}
        elif api_key_id is not None:
            origins[input_id] = {"kind": "api", "label": "API 事件", "target": None}
        elif source not in {"chat", "public_api"}:
            origins[input_id] = {"kind": "system", "label": "系统事件", "target": None}
    return {"origins": origins}
