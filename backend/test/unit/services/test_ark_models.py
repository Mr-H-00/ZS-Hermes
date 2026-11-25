"""方舟混合模型目录的协议回归。"""

import httpx
import pytest

from yuxi.modules.models.providers.ark import is_ark_url
from yuxi.modules.models.providers.cache import ModelInfo, model_cache
from yuxi.modules.models.providers.service import fetch_remote_models, test_model_status_by_spec as check_model_status
from yuxi.modules.models.tables import ModelProvider


# 从测试目录返回真实形状的 HTTP 响应，并拒绝未声明的请求。
@pytest.fixture
def catalog(monkeypatch):
    """准备按目录路径响应的方舟模型供应商。"""
    pages = {}

    async def fetch(client, url, **kwargs):
        """返回所请求路径的模型条目。"""
        request = httpx.Request("GET", url)
        rows = pages.pop(request.url.path)
        return httpx.Response(200, json={"data": rows}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fetch)
    provider = ModelProvider(
        provider_id="user-defined",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        models_endpoint="/models",
        embedding_models_endpoint="/embeddings/models",
        capabilities=["chat", "embedding", "rerank", "image", "video", "audio", "seed3D"],
    )
    return provider, pages


# URL 协议识别也用于缓存中的其他供应商；损坏的地址不能中断缓存装配。
def test_invalid_provider_url_is_not_an_ark_endpoint():
    """不能解析的 URL 不适用方舟能力。"""
    assert not is_ark_url("https://[invalid")


# 综合目录包含多类模型，个别条目没有可执行能力证据。
@pytest.mark.asyncio
async def test_ark_mixed_catalog_preserves_types_limits_and_embedding_protocol(catalog):
    """单个目录按官方能力与参数映射，名称相似的条目也必须分别判断。"""
    provider, pages = catalog
    pages["/api/v3/models"] = [
        {
            "id": "chat",
            "api_supported": {"chat/completions": True},
            "context_window": 262144,
            "token_limits": {"max_output_token_length": 131072},
            "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        },
        {
            "id": "vision",
            "api_supported": {"embeddings/multimodal": True, "embeddings": False},
            "modalities": {"output_modalities": ["embedding"]},
            "token_limits": {"context_window": 131072},
        },
        {
            "id": "either",
            "api_supported": {"embeddings": True, "embeddings/multimodal": True},
            "dimension": 2048,
        },
        {"id": "rank", "api_supported": {"rerank": True}},
        {"id": "paint", "domain": "ImageGeneration"},
        {"id": "task", "task_type": ["text_to_image", "image_to_image"]},
        {"id": "vector", "modalities": {"output_modalities": ["embedding"]}, "dimension": True},
        {"id": "clip", "api_supported": {"contents/generations": True}, "modalities": {"output_modalities": ["video"]}},
        {"id": "voice", "api_supported": {"chat/completions": True}, "modalities": {"output_modalities": ["text", "audio"]}},
        {"id": "tts", "task_type": ["text_to_speech"]},
        {"id": "speech", "api_supported": {"audio/speech": True}},
        {
            "id": "listen",
            "task_type": ["speech_to_text", "text_generation"],
            "api_supported": {"chat/completions": True},
            "modalities": {"input_modalities": ["audio"], "output_modalities": ["text"]},
        },
        {"id": "mesh", "task_type": ["image_to_3D"]},
        {"id": "hitem3d-2-0-251223"},
        {"id": "hyper3d-gen2-260112"},
        {"id": "doubao-seed-translation-250915"},
        {"id": "doubao-seed-translation-preview-unverified"},
        {"id": "explicit", "type": "rerank"},
        {"id": "chat-rerank-embedding", "api_supported": {"rerank": False}, "context_window": -1},
    ]
    models = {m["id"]: m for m in await fetch_remote_models(provider)}
    assert {key: value["type"] for key, value in models.items()} == {
        "chat": "chat",
        "vision": "embedding",
        "either": "embedding",
        "rank": "rerank",
        "paint": "image",
        "task": "image",
        "vector": "embedding",
        "clip": "video",
        "voice": "audio",
        "tts": "audio",
        "speech": "audio",
        "listen": "chat",
        "mesh": "seed3D",
        "explicit": "rerank",
        "chat-rerank-embedding": "unknown",
        "hitem3d-2-0-251223": "seed3D",
        "hyper3d-gen2-260112": "seed3D",
        "doubao-seed-translation-250915": "chat",
        "doubao-seed-translation-preview-unverified": "unknown",
    }
    assert models["chat"]["context_length"] == 262144
    assert models["chat"]["max_completion_tokens"] == 131072
    assert models["chat"]["input_modalities"] == ["text", "image"]
    assert models["chat"]["output_modalities"] == ["text"]
    assert models["vision"]["context_length"] == 131072
    assert models["vision"]["embedding_protocol"] == "ark_multimodal"
    assert models["either"]["dimension"] == 2048
    assert "embedding_protocol" not in models["either"]
    assert "dimension" not in models["vision"] and "dimension" not in models["vector"]
    assert "context_length" not in models["chat-rerank-embedding"]


# 已返回的 Seed Audio 系列即使缺少语音声明，也应出现在语音筛选中。
async def test_ark_seed_audio_family_is_listed_as_audio(catalog):
    """按系列识别上游条目，普通支持语音输入的模型仍归聊天。"""
    provider, pages = catalog
    pages["/api/v3/models"] = [
        {"id": "doubao-seed-audio-fixture", "domain": "LLM", "api_supported": {"chat/completions": True}},
        {"id": "seed-audio"},
        {"id": "doubao-seed-audiobook-fixture"},
        {"id": "text-with-voice-input", "modalities": {"input_modalities": ["audio"], "output_modalities": ["text"]}},
    ]
    assert [(model["id"], model["type"]) for model in await fetch_remote_models(provider)] == [
        ("doubao-seed-audio-fixture", "audio"),
        ("seed-audio", "audio"),
        ("doubao-seed-audiobook-fixture", "unknown"),
        ("text-with-voice-input", "chat"),
    ]


# 同名的 Seedream 4.0 仅保留已确认的版本，其他同名版本仍分别展示。
async def test_ark_seedream_4_catalog_keeps_requested_revision(catalog):
    """仅过滤方舟已退役中的指定 Seedream 4.0 ID。"""
    provider, pages = catalog
    pages["/api/v3/models"] = [
        {"id": "doubao-seedream-4-0-250828", "name": "doubao-seedream-4-0", "domain": "ImageGeneration", "status": "Retiring"},
        {"id": "doubao-seedream-4-0-20260415", "name": "doubao-seedream-4-0", "domain": "ImageGeneration", "status": "Published"},
        {"id": "other-1", "name": "other", "domain": "ImageGeneration"},
        {"id": "other-2", "name": "other", "domain": "ImageGeneration", "status": "Retiring"},
    ]
    assert [(model["id"], model["type"]) for model in await fetch_remote_models(provider)] == [
        ("doubao-seedream-4-0-20260415", "image"),
        ("other-1", "image"),
        ("other-2", "image"),
    ]


# 未提供连接探测协议的模型不能被当做聊天模型调用。
@pytest.mark.parametrize("kind", ["video", "audio", "seed3D"])
async def test_media_model_connection_status_is_unsupported(monkeypatch, kind):
    """连接测试直接报告该类型的探测支持状态。"""
    info = ModelInfo("ark", "media-model", kind, "Media", "test", "https://example.com/v1", "openai")
    monkeypatch.setattr(model_cache, "get_model_info", lambda spec: info)
    monkeypatch.setattr("yuxi.modules.models.chat.select_model", lambda **kwargs: pytest.fail("媒体模型不能走 chat"))
    result = await check_model_status(info.spec)
    assert (result["status"], result["model_type"]) == ("unsupported", kind)


# 用户指定的独立向量和重排目录仍可提供明确的模型类型。
@pytest.mark.asyncio
async def test_ark_honors_dedicated_catalogs(catalog):
    """独立目录允许最小模型记录。"""
    provider, pages = catalog
    provider.embedding_models_endpoint = "/vectors"
    provider.rerank_models_endpoint = "/rankers"
    pages.update({"/api/v3/models": [], "/api/v3/vectors": [{"id": "vector"}], "/api/v3/rankers": [{"id": "rank"}]})
    assert [(m["id"], m["type"]) for m in await fetch_remote_models(provider)] == [("vector", "embedding"), ("rank", "rerank")]


# 所有类型共用一个官方 URL 时，复用目录不会凭空增加未知模型的可执行类型。
@pytest.mark.asyncio
async def test_ark_shared_catalog_keeps_unclassified_model_unknown(catalog):
    """兼容重复配置主目录的绝对路径。"""
    provider, pages = catalog
    provider.embedding_models_endpoint = provider.rerank_models_endpoint = f"{provider.base_url}/models"
    pages["/api/v3/models"] = [{"id": "unclassified", "type": []}]
    assert [(m["id"], m["type"]) for m in await fetch_remote_models(provider)] == [("unclassified", "unknown")]


# 相似域名仍按通用 OpenAI 接口规则加载。
@pytest.mark.asyncio
async def test_other_catalog_retains_openai_endpoint_defaults(catalog):
    """第三方域名的目录仍按各端点的类型分类。"""
    provider, pages = catalog
    provider.base_url = "https://ark.cn-beijing.volces.com.example.org/api/v3"
    pages.update(
        {
            "/api/v3/models": [{"id": "plain"}, {"id": "doubao-seedream-4-0-250828", "type": "image"}],
            "/api/v3/embeddings/models": [{"id": "vector"}],
        }
    )
    assert [(m["id"], m["type"]) for m in await fetch_remote_models(provider)] == [
        ("plain", "chat"),
        ("doubao-seedream-4-0-250828", "image"),
        ("vector", "embedding"),
    ]
