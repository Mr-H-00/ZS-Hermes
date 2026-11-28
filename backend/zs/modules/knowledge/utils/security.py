"""知识库配置的敏感信息处理工具。"""

from typing import Any

_SENSITIVE_PARAMETER_MARKERS = ("token", "secret", "password", "api_key")


def redact_sensitive_params(params: dict[str, Any]) -> dict[str, Any]:
    """移除知识库类型参数中的敏感凭据。"""
    return {key: value for key, value in params.items() if not any(marker in key.lower() for marker in _SENSITIVE_PARAMETER_MARKERS)}
