from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
import yuxi.modules.models.providers.cache as cache_module
from yuxi.modules.models.providers.cache import REDIS_CACHE_KEY, ModelCache, ModelInfo
from yuxi.modules.models.providers.service import test_model_status_by_spec as check_model_status
from yuxi.modules.models.tables import ModelProvider

pytestmark = pytest.mark.unit


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.get_calls = 0

    def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True


def _patch_redis(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    @contextmanager
    def fake_sync_redis_client(*args, **kwargs):
        del args, kwargs
        yield redis

    monkeypatch.setattr(cache_module, "sync_redis_client", fake_sync_redis_client)


def test_model_cache_prefers_model_base_url_override(monkeypatch):
    saved_cache = {}

    class Provider:
        is_enabled = True
        provider_id = "alibaba-cn"
        api_key = "sk-test"
        api_key_env = None
        provider_type = "openai"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        embedding_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        rerank_base_url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
        headers_json = {}
        extra_json = {}
        include_user_uid = True
        enabled_models = [
            {
                "id": "qwen3-rerank",
                "type": "rerank",
                "display_name": "Qwen3 Rerank",
                "base_url_override": "https://invalid.example/rerank",
            }
        ]

    cache = ModelCache()
    monkeypatch.setattr(cache, "_save_cache", lambda data: saved_cache.update(data))

    cache.rebuild([Provider()])

    assert saved_cache["alibaba-cn:qwen3-rerank"].base_url == "https://invalid.example/rerank"
    assert saved_cache["alibaba-cn:qwen3-rerank"].include_user_uid is True


def test_model_cache_loads_from_redis_and_uses_local_ttl(monkeypatch: pytest.MonkeyPatch):
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    redis.data[REDIS_CACHE_KEY] = json.dumps(
        {
            "provider:chat": {
                "provider_id": "provider",
                "model_id": "chat",
                "model_type": "chat",
                "display_name": "Chat",
                "api_key": "sk-test",
                "base_url": "https://example.com/v1",
                "provider_type": "openai",
                "request_body_overrides": {"enable_thinking": False},
            }
        }
    )
    cache = ModelCache()

    info = cache.get_model_info("provider:chat")
    cached_info = cache.get_model_info("provider:chat")

    assert info is not None
    assert cached_info is info
    assert info.base_url == "https://example.com/v1"
    assert info.request_body_overrides == {"enable_thinking": False}
    assert redis.get_calls == 1


# 从模型配置重建缓存并回读向量请求地址，覆盖方舟两种协议及显式地址。
def test_ark_embedding_cache_resolves_model_protocol_and_respects_overrides(monkeypatch):
    """验证方舟目录导入后每个模型使用自己的执行地址。"""
    _patch_redis(monkeypatch, _FakeRedis())
    base = "https://ark.cn-beijing.volces.com/api/v3"
    custom = "https://gateway.example/vectors"
    provider = ModelProvider(
        provider_id="custom-ark",
        base_url=base + "/",
        provider_type="openai",
        is_enabled=True,
        enabled_models=[
            {"id": "text", "type": "embedding"},
            {"id": "vision", "type": "embedding", "embedding_protocol": "ark_multimodal"},
            {"id": "custom", "type": "embedding", "base_url_override": custom},
            {"id": "chat", "type": "chat"},
            {"id": "rank", "type": "rerank"},
        ],
    )
    cache = ModelCache()
    cache.rebuild([provider])
    assert cache.get_model_info("custom-ark:text").base_url == base + "/embeddings"
    assert cache.get_model_info("custom-ark:vision").base_url == base + "/embeddings/multimodal"
    assert cache.get_model_info("custom-ark:custom").base_url == custom
    assert cache.get_model_info("custom-ark:chat").base_url == base + "/"
    assert cache.get_model_info("custom-ark:rank").base_url == base + "/rerank"

    provider.embedding_base_url = custom
    provider.rerank_base_url = "https://gateway.example/rank"
    cache.rebuild([provider])
    assert cache.get_model_info("custom-ark:vision").base_url == custom
    assert cache.get_model_info("custom-ark:rank").base_url == "https://gateway.example/rank"


# 其他供应商可以在同名路径上提供标准的 OpenAI 向量接口。
def test_custom_multimodal_path_keeps_standard_embedding_protocol(monkeypatch):
    """第三方同名路径仍使用逐条文本的标准批量协议。"""
    from yuxi.modules.models.embed import select_embedding_model

    info = ModelInfo("custom", "text", "embedding", "text", "test", "https://custom.example/embeddings/multimodal", "openai")
    monkeypatch.setattr(cache_module.model_cache, "get_model_info", lambda spec: info)
    assert select_embedding_model(info.spec).build_payload(["a", "b"]) == {"model": "text", "input": ["a", "b"]}


# 通过已启用模型的状态接口读取一个有效数值向量的长度。
@pytest.mark.parametrize(("vector", "dimension"), [([0.1, 0.2], 2), ([], None), ("bad", None), ([True], None)])
async def test_ark_status_measures_only_valid_vectors(monkeypatch, httpx_mock, vector, dimension):
    """验证缺少目录维度时的单次推理结果和非法响应。"""
    url = "https://ark.cn-beijing.volces.com/api/v3/embeddings"
    info = ModelInfo("custom-ark", "text", "embedding", "text", "test-key", url, "openai")
    monkeypatch.setattr(cache_module.model_cache, "get_model_info", lambda spec: info)
    httpx_mock.add_response(method="POST", url=url, json={"data": [{"embedding": vector}]})
    result = await check_model_status(info.spec)
    assert result["status"] == ("available" if dimension else "unavailable")
    assert result.get("dimension") == dimension


def test_model_cache_save_writes_redis_json(monkeypatch: pytest.MonkeyPatch):
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    cache = ModelCache()
    info = ModelInfo(
        provider_id="provider",
        model_id="chat",
        model_type="chat",
        display_name="Chat",
        api_key="sk-test",
        base_url="https://example.com/v1",
        provider_type="openai",
        request_body_overrides={"enable_thinking": True},
        include_user_uid=True,
    )

    cache._save_cache({info.spec: info})

    payload = json.loads(redis.data[REDIS_CACHE_KEY])
    assert payload[info.spec]["base_url"] == "https://example.com/v1"
    assert payload[info.spec]["request_body_overrides"] == {"enable_thinking": True}
    assert payload[info.spec]["include_user_uid"] is True

    reloaded = ModelCache()
    assert reloaded.get_model_info("provider:chat").include_user_uid is True


def test_model_cache_defaults_include_user_uid_to_false(monkeypatch: pytest.MonkeyPatch):
    """存量 Redis 缓存缺省该字段时按关闭处理，不因 KeyError 中断模型加载。"""
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    redis.data[REDIS_CACHE_KEY] = json.dumps(
        {
            "provider:chat": {
                "provider_id": "provider",
                "model_id": "chat",
                "model_type": "chat",
                "display_name": "Chat",
                "api_key": "sk-test",
                "base_url": "https://example.com/v1",
                "provider_type": "openai",
            }
        }
    )

    info = ModelCache().get_model_info("provider:chat")
    assert info.include_user_uid is False


# 恢复升级前的 Redis 快照，验证两种查询名称都能查到同一图片模型。
def test_legacy_image_cache_snapshot_matches_both_catalog_queries(monkeypatch):
    """旧缓存恢复后使用新类型名分组，兼容旧列表请求。"""
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    info = ModelInfo("paint", "model", "image_generation", "图片", "test", "https://example.com/v1", "openai")
    redis.data[REDIS_CACHE_KEY] = json.dumps({info.spec: info.to_dict()})

    cache = ModelCache()
    for model_type in ("image", "image_generation"):
        assert [item.spec for item in cache.get_all_specs(model_type)] == ["paint:model"]
        assert cache.get_specs_grouped_by_provider(model_type)["paint"][0].model_type == "image"


@pytest.mark.parametrize("failure", [ConnectionError("redis down"), ValueError("invalid cache payload")])
def test_model_cache_failure_is_not_reported_as_missing_model(monkeypatch, failure):
    """基础设施与缓存载荷失败必须保留明确故障语义。"""
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    monkeypatch.setattr(redis, "get", lambda _key: (_ for _ in ()).throw(failure))
    cache = ModelCache()
    with pytest.raises(RuntimeError, match="模型缓存不可用") as caught:
        cache.get_model_info("provider:chat")
    assert caught.value.__cause__ is failure
    assert cache._local_cache is None


def test_model_cache_recovers_after_failure_and_accepts_explicit_empty_config(monkeypatch):
    """读取失败不缓存空配置，Redis 恢复后可以重新读取。"""
    redis = _FakeRedis()
    _patch_redis(monkeypatch, redis)
    original_get = redis.get
    monkeypatch.setattr(redis, "get", lambda _key: (_ for _ in ()).throw(ConnectionError("redis down")))
    cache = ModelCache()
    with pytest.raises(RuntimeError, match="模型缓存不可用"):
        cache.get_all_specs()
    monkeypatch.setattr(redis, "get", original_get)
    redis.data[REDIS_CACHE_KEY] = "{}"
    assert cache.get_all_specs() == []


def test_model_cache_missing_snapshot_requires_initialization(monkeypatch):
    """缺少缓存快照不等同于已配置零个模型。"""
    _patch_redis(monkeypatch, _FakeRedis())
    with pytest.raises(RuntimeError, match="模型缓存不可用"):
        ModelCache().get_all_specs()
