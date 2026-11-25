"""用本地 HTTP 服务验证方舟多模态向量协议。"""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from yuxi.modules.models.embed import select_embedding_model
from yuxi.modules.models.providers.cache import ModelInfo, model_cache

pytestmark = [pytest.mark.integration, pytest.mark.self_managed_resources, pytest.mark.asyncio]


# 同步和异步调用各自经过真实 HTTP，比较逐条文本及返回向量。
@pytest.mark.parametrize("use_async", [False, True])
async def test_ark_multimodal_batch_keeps_documents_separate(monkeypatch, use_async):
    """每个文档独立编码，已配置维度进入请求并约束响应。"""
    bodies = []

    # 记录请求体并模拟方舟按单个文档返回对象的响应格式。
    async def embed(request: web.Request) -> web.Response:
        """按输入文本回放固定向量，包括上游返回错误维度的情况。"""
        body = await request.json()
        bodies.append(body)
        parts = body.get("input")
        if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
            return web.json_response({"error": "expected one text part"}, status=422)
        if parts[0]["text"] == "missing":
            return web.json_response({"data": []})
        vectors = {"first": [0.2, 0.1], "second": [0.7, 0.5], "bad": [0.0], "invalid": [True, None]}
        return web.json_response({"data": {"embedding": vectors[parts[0]["text"]]}})

    app = web.Application()
    app.router.add_post("/api/v3/embeddings/multimodal", embed)
    monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
    async with TestServer(app) as server:
        # 保留选择器看到的官方 URL，只将实际 HTTP 连接指向测试服务器。
        endpoint = "/api/v3/embeddings/multimodal"
        monkeypatch.setattr("yuxi.modules.models.embed.get_docker_safe_url", lambda url: str(server.make_url(endpoint)))
        info = ModelInfo(
            provider_id="example",
            provider_type="openai",
            model_type="embedding",
            model_id="example-vector",
            display_name="Example",
            api_key="test",
            base_url=f"https://ark.cn-beijing.volces.com{endpoint}",
            dimension=2,
        )
        monkeypatch.setattr(model_cache, "get_model_info", lambda spec: info)
        model = select_embedding_model(info.spec)
        if use_async:
            result = await model.abatch_encode(["first", "second"])
        else:
            result = await asyncio.to_thread(model.batch_encode, ["first", "second"])
        assert result == [[0.2, 0.1], [0.7, 0.5]]
        assert bodies == [
            {"model": "example-vector", "dimensions": 2, "input": [{"type": "text", "text": text}]} for text in ["first", "second"]
        ]
        with pytest.raises(ValueError, match="dimension"):
            await model.aencode("bad")
        with pytest.raises(ValueError, match="data.embedding"):
            await model.aencode("missing")
        with pytest.raises(ValueError, match="无效数值"):
            await model.aencode("invalid")
