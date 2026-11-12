"""从模型标准块投影展示正文。"""

import os

from yuxi.infrastructure.observability.logging import logger


def get_docker_safe_url(base_url):
    """在容器内将本机模型地址指向宿主机。"""
    if not base_url:
        return base_url
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        base_url = base_url.replace("http://localhost", "http://host.docker.internal")
        base_url = base_url.replace("http://127.0.0.1", "http://host.docker.internal")
        logger.info(f"Running in docker, using {base_url} as base url")
    return base_url


def parse_assistant_message_body(content: str | list) -> dict[str, str]:
    """将标准 text/reasoning 块投影为展示正文。"""
    blocks = content if isinstance(content, list) else []
    text_parts = []
    reasoning_parts = []
    for block in blocks:
        match block:
            case {"type": "text", "text": str(value)}:
                text_parts.append(value)
            case {"type": "reasoning", "reasoning": str(value)}:
                reasoning_parts.append(value)

    text = content if isinstance(content, str) else "".join(text_parts)
    return {"content": text, "reasoning_content": "".join(reasoning_parts)}
