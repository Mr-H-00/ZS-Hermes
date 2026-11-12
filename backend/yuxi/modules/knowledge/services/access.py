"""知识库当前读取权限与调用方选择的交集。"""

from typing import Any, Literal


async def visible_knowledge_bases(
    uid: str,
    *,
    selection: Literal["all"] | list[str] = "all",
) -> list[dict[str, Any]]:
    """按当前用户权限解析全部或显式选择的知识库摘要。"""
    if selection != "all" and (
        not isinstance(selection, list) or any(not isinstance(kb_id, str) or not kb_id.strip() for kb_id in selection)
    ):
        raise ValueError('知识库选择必须是"all" 或非空字符串组成的列表')
    if not uid or selection == []:
        return []

    from yuxi.modules.knowledge.runtime import knowledge_base

    summaries = await knowledge_base.get_knowledge_bases_by_uid(uid)
    selected_ids = None if selection == "all" else {kb_id.strip() for kb_id in selection}
    return [
        {"kb_id": item.kb_id, "name": item.name, "description": item.description, "kb_type": item.kb_type}
        for item in summaries
        if selected_ids is None or item.kb_id in selected_ids
    ]
