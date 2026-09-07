"""Parent-Child 缓存的真实 Redis 集成测试。"""

import uuid

import pytest

from yuxi.knowledge.parent_child_cache import (
    PARENT_CACHE_TTL_SECONDS,
    QUERY_CACHE_TTL_SECONDS,
    cache_parent,
    cache_query,
    get_cached_parent,
    get_cached_query,
    invalidate_parent_cache,
    invalidate_query_cache,
    parent_cache_key,
    query_cache_key,
)
from yuxi.storage.redis import get_async_redis_client

pytestmark = pytest.mark.asyncio


async def test_parent_child_cache_round_trip_ttl_and_scoped_invalidation() -> None:
    """真实 Redis 应保存 TTL，并只清理目标知识库的缓存。"""
    scope = f"redis-integration-{uuid.uuid4().hex}"
    other_scope = f"redis-integration-{uuid.uuid4().hex}"
    parent_key = parent_cache_key(scope, "v1", "p1")
    query_key = query_cache_key(scope, "v1", "fingerprint")
    other_parent_key = parent_cache_key(other_scope, "v1", "p2")
    other_query_key = query_cache_key(other_scope, "v1", "fingerprint")
    redis = await get_async_redis_client()

    try:
        await cache_parent(scope, "v1", "p1", {"parent_text": "parent"})
        await cache_query(scope, "v1", "fingerprint", [{"id": "p1"}])
        await cache_parent(other_scope, "v1", "p2", {"parent_text": "other"})
        await cache_query(other_scope, "v1", "fingerprint", [{"id": "p2"}])

        assert await get_cached_parent(scope, "v1", "p1") == {"parent_text": "parent"}
        assert await get_cached_query(scope, "v1", "fingerprint") == [{"id": "p1"}]
        assert 0 < await redis.ttl(parent_key) <= PARENT_CACHE_TTL_SECONDS
        assert 0 < await redis.ttl(query_key) <= QUERY_CACHE_TTL_SECONDS

        await invalidate_parent_cache(scope)
        await invalidate_query_cache(scope)

        assert await redis.exists(parent_key) == 0
        assert await redis.exists(query_key) == 0
        assert await redis.exists(other_parent_key) == 1
        assert await redis.exists(other_query_key) == 1
    finally:
        await redis.delete(parent_key, query_key, other_parent_key, other_query_key)
