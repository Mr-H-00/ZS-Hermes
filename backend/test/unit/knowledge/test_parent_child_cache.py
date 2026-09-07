import json

import pytest

from yuxi.knowledge import parent_child_cache as cache


class _Redis:
    """提供父块缓存测试需要的最小异步 Redis。"""

    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.set_calls.append((key, ex))

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def scan_iter(self, match):
        prefix = match[:-1]
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


def test_parent_child_cache_keys_include_scope_and_identity():
    """缓存 key 必须包含 kb_id、version_id 和对象身份。"""
    assert cache.parent_cache_key("kb", "version", "parent") == "yuxi:parent-child:parent:kb:version:parent"
    assert cache.query_cache_key("kb", "version", "query") == "yuxi:parent-child:query:kb:version:query"
    assert cache.query_fingerprint("hello", {"top_k_parent": 2}) == cache.query_fingerprint(
        "hello", {"top_k_parent": 2}
    )


@pytest.mark.asyncio
async def test_parent_cache_round_trip_and_version_invalidation(monkeypatch):
    """父块缓存可读写并仅清理目标版本。"""
    redis = _Redis()
    monkeypatch.setattr(cache, "get_async_redis_client", lambda: _async_value(redis))
    await cache.cache_parent("kb", "v1", "p1", {"parent_text": "text"})
    await cache.cache_parent("kb", "v2", "p2", {"parent_text": "other"})
    assert redis.set_calls == [
        (cache.parent_cache_key("kb", "v1", "p1"), cache.PARENT_CACHE_TTL_SECONDS),
        (cache.parent_cache_key("kb", "v2", "p2"), cache.PARENT_CACHE_TTL_SECONDS),
    ]
    assert await cache.get_cached_parent("kb", "v1", "p1") == {"parent_text": "text"}

    await cache.invalidate_parent_version("kb", "v1")

    assert await cache.get_cached_parent("kb", "v1", "p1") is None
    assert await cache.get_cached_parent("kb", "v2", "p2") == {"parent_text": "other"}


@pytest.mark.asyncio
async def test_query_cache_round_trip_and_scope_invalidation(monkeypatch):
    """查询缓存可读写，并按知识库清理而不影响其他知识库。"""
    redis = _Redis()
    monkeypatch.setattr(cache, "get_async_redis_client", lambda: _async_value(redis))
    fingerprint = cache.query_fingerprint("q", {"final_top_k": 1})
    await cache.cache_query("kb", "active", fingerprint, [{"id": "p1"}])
    await cache.cache_query("other", "active", fingerprint, [{"id": "p2"}])
    assert redis.set_calls == [
        (cache.query_cache_key("kb", "active", fingerprint), cache.QUERY_CACHE_TTL_SECONDS),
        (cache.query_cache_key("other", "active", fingerprint), cache.QUERY_CACHE_TTL_SECONDS),
    ]

    assert await cache.get_cached_query("kb", "active", fingerprint) == [{"id": "p1"}]
    await cache.invalidate_query_cache("kb")
    assert await cache.get_cached_query("kb", "active", fingerprint) is None
    assert await cache.get_cached_query("other", "active", fingerprint) == [{"id": "p2"}]


@pytest.mark.asyncio
async def test_parent_cache_scope_invalidation_clears_all_versions(monkeypatch):
    """知识库删除时应清理该知识库全部版本的父块缓存。"""
    redis = _Redis()
    monkeypatch.setattr(cache, "get_async_redis_client", lambda: _async_value(redis))
    await cache.cache_parent("kb", "v1", "p1", {"parent_text": "one"})
    await cache.cache_parent("kb", "v2", "p2", {"parent_text": "two"})
    await cache.cache_parent("other", "v1", "p3", {"parent_text": "other"})

    await cache.invalidate_parent_cache("kb")

    assert await cache.get_cached_parent("kb", "v1", "p1") is None
    assert await cache.get_cached_parent("kb", "v2", "p2") is None
    assert await cache.get_cached_parent("other", "v1", "p3") == {"parent_text": "other"}


@pytest.mark.asyncio
async def test_malformed_cache_payloads_are_misses(monkeypatch):
    """损坏 JSON 和非对象查询列表都必须按未命中处理。"""
    redis = _Redis()
    monkeypatch.setattr(cache, "get_async_redis_client", lambda: _async_value(redis))
    parent_key = cache.parent_cache_key("kb", "v1", "p1")
    query_key = cache.query_cache_key("kb", "v1", "fingerprint")

    redis.values[parent_key] = "{invalid"
    redis.values[query_key] = json.dumps({"id": "not-a-list"})
    assert await cache.get_cached_parent("kb", "v1", "p1") is None
    assert await cache.get_cached_query("kb", "v1", "fingerprint") is None

    redis.values[parent_key] = json.dumps([{"parent_text": "not-a-dict"}])
    redis.values[query_key] = json.dumps([{"id": "valid"}, "invalid"])
    assert await cache.get_cached_parent("kb", "v1", "p1") is None
    assert await cache.get_cached_query("kb", "v1", "fingerprint") is None

    redis.values[parent_key] = json.dumps({"parent_text": None})
    assert await cache.get_cached_parent("kb", "v1", "p1") is None


@pytest.mark.asyncio
async def test_redis_errors_are_cache_misses_and_do_not_break_cleanup(monkeypatch):
    """Redis 读写和删除异常不得阻断 PostgreSQL/Milvus 回源或清理。"""

    class FailingRedis:
        async def get(self, _key):
            raise RuntimeError("redis unavailable")

        async def set(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")

        async def delete(self, *_keys):
            raise RuntimeError("redis unavailable")

        async def scan_iter(self, match):
            del match
            yield "cached-key"

    monkeypatch.setattr(cache, "get_async_redis_client", lambda: _async_value(FailingRedis()))

    assert await cache.get_cached_parent("kb", "v1", "p1") is None
    assert await cache.get_cached_query("kb", "v1", "fingerprint") is None
    await cache.cache_parent("kb", "v1", "p1", {"parent_text": "text"})
    await cache.cache_query("kb", "v1", "fingerprint", [{"id": "p1"}])
    await cache.invalidate_parent_version("kb", "v1")
    await cache.invalidate_parent_cache("kb")
    await cache.invalidate_query_cache("kb")


async def _async_value(value):
    """返回异步替身结果。"""
    return value
