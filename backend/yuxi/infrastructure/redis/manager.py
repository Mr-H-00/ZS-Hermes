"""Redis 客户端管理。

本模块只负责 Redis 连接参数、客户端创建和连接生命周期；业务 key、TTL、序列化格式
留在调用方模块中。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from typing import Any, Self
from urllib.parse import urlparse, urlunparse

from yuxi.infrastructure.runtime_settings import get_int_env

DEFAULT_REDIS_URL = "redis://redis:6379/0"
DEFAULT_REDIS_MAX_CONNECTIONS = 32

_async_redis_client: Any | None = None
_async_redis_lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class RedisConfig:
    """Redis 连接配置，仅承载参数，不建立连接。"""

    url: str = DEFAULT_REDIS_URL
    max_connections: int = DEFAULT_REDIS_MAX_CONNECTIONS
    decode_responses: bool = True
    socket_timeout: float | None = None
    socket_connect_timeout: float | None = None

    @classmethod
    def from_env(
        cls,
        *,
        decode_responses: bool | None = None,
        socket_timeout: float | None = None,
        socket_connect_timeout: float | None = None,
    ) -> Self:
        """从部署环境读取连接配置，显式参数优先于环境变量。"""
        url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        max_connections = get_int_env("REDIS_MAX_CONNECTIONS", DEFAULT_REDIS_MAX_CONNECTIONS)
        resolved_decode_responses = True if decode_responses is None else decode_responses
        resolved_socket_timeout = socket_timeout if socket_timeout is not None else _float_or_none(os.environ.get("REDIS_SOCKET_TIMEOUT"))
        resolved_connect_timeout = (
            socket_connect_timeout if socket_connect_timeout is not None else _float_or_none(os.environ.get("REDIS_CONNECT_TIMEOUT"))
        )
        return cls(
            url=url,
            max_connections=max_connections,
            decode_responses=resolved_decode_responses,
            socket_timeout=resolved_socket_timeout,
            socket_connect_timeout=resolved_connect_timeout,
        )

    @property
    def log_url(self) -> str:
        """返回可安全写入日志的连接地址。"""
        return redact_redis_url(self.url)

    def connection_kwargs(self) -> dict[str, Any]:
        """返回底层 Redis 客户端需要的连接参数。"""
        kwargs: dict[str, Any] = {
            "decode_responses": self.decode_responses,
            "max_connections": self.max_connections,
        }
        if self.socket_timeout is not None:
            kwargs["socket_timeout"] = self.socket_timeout
        if self.socket_connect_timeout is not None:
            kwargs["socket_connect_timeout"] = self.socket_connect_timeout
        return kwargs


def redact_redis_url(url: str) -> str:
    """隐藏 URL 中的密码用于日志输出；脱敏失败时原样返回。"""
    try:
        parsed = urlparse(url)
        if parsed.password:
            userinfo, _, host = parsed.netloc.rpartition("@")
            username, _, _ = userinfo.partition(":")
            parsed = parsed._replace(netloc=f"{username}:***@{host}")
        return urlunparse(parsed)
    except Exception:
        return url


def create_sync_redis_client(config: RedisConfig | None = None, *, ping: bool = True) -> Any:
    """创建同步 Redis 客户端。调用方按自身生命周期缓存或关闭。"""
    config = config or RedisConfig.from_env()
    try:
        import redis
    except Exception as e:
        raise RuntimeError("redis dependency is required") from e

    client = redis.from_url(config.url, **config.connection_kwargs())
    if not ping:
        return client

    try:
        client.ping()
    except Exception as e:
        _close_sync_client(client)
        raise RuntimeError(f"Redis connection failed ({config.log_url}): {e}") from e
    return client


@contextmanager
def sync_redis_client(config: RedisConfig | None = None, *, ping: bool = True) -> Iterator[Any]:
    """短生命周期同步 Redis 客户端。"""
    client = create_sync_redis_client(config, ping=ping)
    try:
        yield client
    finally:
        _close_sync_client(client)


async def create_async_redis_client(config: RedisConfig | None = None, *, ping: bool = True) -> Any:
    """创建异步 Redis 客户端。调用方按自身生命周期缓存或关闭。"""
    config = config or RedisConfig.from_env()
    try:
        from redis.asyncio import Redis
    except Exception as e:
        raise RuntimeError("redis dependency is required") from e

    client = Redis.from_url(config.url, **config.connection_kwargs())
    if not ping:
        return client

    try:
        await client.ping()
    except Exception as e:
        await _close_async_client(client)
        raise RuntimeError(f"Redis connection failed ({config.log_url}): {e}") from e
    return client


async def get_async_redis_client(config: RedisConfig | None = None) -> Any:
    """获取共享异步 Redis 客户端。"""
    global _async_redis_client
    if _async_redis_client is not None:
        return _async_redis_client

    async with _get_async_redis_lock():
        if _async_redis_client is None:
            _async_redis_client = await create_async_redis_client(config)
        return _async_redis_client


async def close_async_redis_client() -> None:
    """关闭共享异步 Redis 客户端。"""
    global _async_redis_client
    if _async_redis_client is None:
        return
    await _close_async_client(_async_redis_client)
    _async_redis_client = None


def get_arq_redis_settings(config: RedisConfig | None = None) -> Any:
    """创建 ARQ 使用的 RedisSettings。"""
    config = config or RedisConfig.from_env()
    try:
        from arq.connections import RedisSettings
    except Exception as e:
        raise RuntimeError("arq dependency is required") from e
    return replace(
        RedisSettings.from_dsn(config.url),
        max_connections=config.max_connections,
    )


async def create_arq_redis_pool(config: RedisConfig | None = None) -> Any:
    """创建 ARQ Redis 连接池。"""
    try:
        from arq.connections import create_pool
    except Exception as e:
        raise RuntimeError("arq dependency is required") from e
    return await create_pool(get_arq_redis_settings(config))


def _float_or_none(value: str | None) -> float | None:
    """把可选环境变量转换为浮点数。"""
    if value is None or not value.strip():
        return None
    return float(value)


def _close_sync_client(client: Any) -> None:
    """尽力关闭同步客户端，不遮蔽调用方的主异常。"""
    with suppress(Exception):
        client.close()


async def _close_async_client(client: Any) -> None:
    """尽力关闭异步客户端，不遮蔽调用方的主异常。"""
    with suppress(Exception):
        await client.aclose()


def _get_async_redis_lock() -> asyncio.Lock:
    """返回进程内共享异步客户端的初始化锁。"""
    global _async_redis_lock
    if _async_redis_lock is None:
        _async_redis_lock = asyncio.Lock()
    return _async_redis_lock
