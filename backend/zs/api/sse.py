"""SSE 事件流的共享格式化工具。"""

import json


def format_sse(data: dict, event: str, event_id: str | None = None) -> str:
    """把事件数据格式化为 SSE 帧；提供 event_id 时携带游标行。"""
    lines = [f"event: {event}", f"data: {json.dumps(data, ensure_ascii=False)}"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append("")
    return "\n".join(lines) + "\n"
