"""业务 PostgreSQL 映射。"""

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
)

from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import format_utc_datetime, utc_now

_SENSITIVE_KEY_PARTS = ("authorization", "api_key", "apikey", "token", "secret", "password", "cookie")


def is_sensitive_config_key(key: str) -> bool:
    """判断管理响应中需要遮蔽的配置键。"""
    normalized = key.replace("-", "_").lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_value(value: Any) -> Any:
    """递归遮蔽动态配置中的凭据字段。"""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if is_sensitive_config_key(str(key)) else _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


class ModelProvider(Base):
    """模型供应商配置，存储 provider 基础信息、模型端点和可用模型。"""

    __tablename__ = "model_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String(100), nullable=False, unique=True, index=True, comment="供应商稳定标识")
    display_name = Column(String(100), nullable=False, comment="展示名称")
    provider_type = Column(String(32), nullable=False, default="openai", comment="供应商适配类型，默认 openai")

    default_protocol = Column(String(64), nullable=True, comment="默认协议，如 openai_compatible")
    base_url = Column(String(500), nullable=False, comment="API 基础 URL")
    embedding_base_url = Column(String(500), nullable=True, comment="Embedding 模型请求基础 URL")
    rerank_base_url = Column(String(500), nullable=True, comment="Rerank 模型请求基础 URL")
    models_endpoint = Column(String(200), nullable=True, comment="聊天/通用模型列表端点")
    embedding_models_endpoint = Column(String(200), nullable=True, comment="Embedding 模型列表端点")
    rerank_models_endpoint = Column(String(200), nullable=True, comment="Rerank 模型列表端点")
    api_key_env = Column(String(128), nullable=True, comment="API Key 环境变量名")
    api_key = Column(String(500), nullable=True, comment="直接配置的 API Key")

    capabilities = Column(JSON, nullable=False, default=list, comment="支持能力：chat/embedding/rerank")
    enabled_models = Column(JSON, nullable=False, default=list, comment="已启用模型配置对象")
    headers_json = Column(JSON, nullable=True, comment="额外请求头")
    extra_json = Column(JSON, nullable=True, comment="扩展配置")

    is_enabled = Column(Boolean, nullable=False, default=True, index=True, comment="供应商是否启用")
    is_builtin = Column(Boolean, nullable=False, default=False, comment="是否内置")
    include_user_uid = Column(Boolean, nullable=False, default=False, comment="聊天模型请求是否注入带签名的用户 UID 头")

    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        """返回管理端可见配置；凭据只保留是否已配置的事实。"""
        safe_headers = _redact_value(self.headers_json or {})
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_type": self.provider_type,
            "default_protocol": self.default_protocol,
            "base_url": self.base_url,
            "embedding_base_url": self.embedding_base_url,
            "rerank_base_url": self.rerank_base_url,
            "models_endpoint": self.models_endpoint,
            "embedding_models_endpoint": self.embedding_models_endpoint,
            "rerank_models_endpoint": self.rerank_models_endpoint,
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "capabilities": self.capabilities or [],
            "enabled_models": self.enabled_models or [],
            "headers_json": safe_headers,
            "extra_json": _redact_value(self.extra_json or {}),
            "is_enabled": bool(self.is_enabled),
            "is_builtin": bool(self.is_builtin),
            "include_user_uid": bool(self.include_user_uid),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }
