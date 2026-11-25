"""从模型标准块投影展示正文。"""

import os
import re
from typing import overload


@overload
def get_docker_safe_url(base_url: str) -> str: ...


@overload
def get_docker_safe_url(base_url: None) -> None: ...


def get_docker_safe_url(base_url: str | None) -> str | None:
    """在容器内将本机模型地址指向宿主机。"""
    if not base_url:
        return base_url
    if os.getenv("RUNNING_IN_DOCKER") == "true":
        return re.sub(r"^http://(?:localhost|127\.0\.0\.1)(?=:[0-9]+(?:[/?#]|$)|[/?#]|$)", "http://host.docker.internal", base_url)
    return base_url


def strip_chat_reasoning_tags(content: str) -> str:
    """从只展示正文的短文本结果中移除供应商内联推理。"""
    content = re.sub(r"<think>.*?(?:</think>|$)", "", content, flags=re.IGNORECASE | re.DOTALL)
    return re.split(r"</think>", content, flags=re.IGNORECASE)[-1]


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
