"""Parent-Child 短期缓存；PostgreSQL/Milvus 仍是最终事实来源。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from yuxi.storage.redis import get_async_redis_client
from yuxi.utils.logging_config import logger

CACHE_PREFIX = "yuxi:parent-child"
PARENT_CACHE_TTL_SECONDS = 3600
QUERY_CACHE_TTL_SECONDS = 600


def parent_cache_key(kb_id: str, version_id: str, parent_id: str) -> str:
    """生成包含知识库、版本和父块身份的缓存 key。"""
    return f"{CACHE_PREFIX}:parent:{kb_id}:{version_id}:{parent_id}"


def query_cache_key(kb_id: str, version_id: str, query_fingerprint: str) -> str:
    """生成包含知识库、版本和查询身份的缓存 key。"""
    return f"{CACHE_PREFIX}:query:{kb_id}:{version_id}:{query_fingerprint}"


def query_fingerprint(query_text: str, params: dict[str, Any]) -> str:
    """为查询文本和有效参数生成稳定的缓存指纹。"""
    payload = json.dumps(
        {"query": query_text, "params": params},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def active_versions_fingerprint(version_ids: list[str]) -> str:
    """为知识库当前 active 版本集合生成稳定作用域。"""
    payload = "\n".join(sorted(str(version_id) for version_id in version_ids)).encode("utf-8")
    return f"vset-{hashlib.sha256(payload).hexdigest()}"


async def get_cached_parent(
    kb_id: str,
    version_id: str,
    parent_id: str,
) -> dict[str, Any] | None:
    """读取父块缓存；Redis 异常按 cache miss 处理。"""
    try:
        raw = await (await get_async_redis_client()).get(parent_cache_key(kb_id, version_id, parent_id))
        value = json.loads(raw) if raw else None
        if not isinstance(value, dict) or not isinstance(value.get("parent_text"), str):
            return None
        return value
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child cache read failed: {exc}")
        return None


async def cache_parent(kb_id: str, version_id: str, parent_id: str, payload: dict[str, Any]) -> None:
    """写入可丢失的父块文本缓存。"""
    try:
        redis = await get_async_redis_client()
        await redis.set(
            parent_cache_key(kb_id, version_id, parent_id),
            json.dumps(payload, ensure_ascii=False),
            ex=PARENT_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child cache write failed: {exc}")


async def get_cached_query(kb_id: str, version_id: str, fingerprint: str) -> list[dict[str, Any]] | None:
    """读取查询结果缓存；Redis 异常按未命中处理。"""
    try:
        raw = await (await get_async_redis_client()).get(query_cache_key(kb_id, version_id, fingerprint))
        value = json.loads(raw) if raw else None
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            return None
        return value
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child query cache read failed: {exc}")
        return None


async def cache_query(kb_id: str, version_id: str, fingerprint: str, payload: list[dict[str, Any]]) -> None:
    """写入可丢失的查询结果缓存。"""
    try:
        redis = await get_async_redis_client()
        await redis.set(
            query_cache_key(kb_id, version_id, fingerprint),
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=QUERY_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child query cache write failed: {exc}")


async def invalidate_parent_version(kb_id: str, version_id: str) -> None:
    """按版本删除父块缓存，避免旧版本继续被读取。"""
    try:
        redis = await get_async_redis_client()
        keys = [key async for key in redis.scan_iter(match=f"{CACHE_PREFIX}:parent:{kb_id}:{version_id}:*")]
        if keys:
            await redis.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child cache invalidation failed: {exc}")


async def invalidate_parent_cache(kb_id: str) -> None:
    """删除知识库全部版本的父块缓存。"""
    try:
        redis = await get_async_redis_client()
        keys = [key async for key in redis.scan_iter(match=f"{CACHE_PREFIX}:parent:{kb_id}:*")]
        if keys:
            await redis.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child cache invalidation failed: {exc}")


async def invalidate_query_cache(kb_id: str, version_id: str | None = None) -> None:
    """按知识库或版本清理查询缓存。"""
    try:
        redis = await get_async_redis_client()
        pattern = f"{CACHE_PREFIX}:query:{kb_id}:{version_id}:*" if version_id else f"{CACHE_PREFIX}:query:{kb_id}:*"
        keys = [key async for key in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Parent-Child query cache invalidation failed: {exc}")


__all__ = [
    "PARENT_CACHE_TTL_SECONDS",
    "QUERY_CACHE_TTL_SECONDS",
    "parent_cache_key",
    "query_cache_key",
    "query_fingerprint",
    "active_versions_fingerprint",
    "get_cached_parent",
    "cache_parent",
    "get_cached_query",
    "cache_query",
    "invalidate_parent_version",
    "invalidate_parent_cache",
    "invalidate_query_cache",
]
