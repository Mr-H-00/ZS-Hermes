"""部署时的可选业务能力装配。"""

import os


def knowledge_enabled() -> bool:
    """读取知识库部署开关；非法值明确阻止启动。"""
    value = os.getenv("ZS_KNOWLEDGE_ENABLED", "true").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("ZS_KNOWLEDGE_ENABLED 必须为 true 或 false")
    return value == "true"
