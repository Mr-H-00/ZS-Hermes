"""使用真实 HTTP、登录令牌和 PostgreSQL 验证绘画用户流程。"""

import base64
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from PIL import Image
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.main import app
from yuxi.infrastructure.redis import sync_redis_client
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.models import images
from yuxi.modules.models.providers import cache
from yuxi.modules.models.tables import ModelProvider

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_images_require_admin_config_and_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置变更经过真实 API，图片及上游请求均由独立结果校验。"""
    upstream = FastAPI()
    observed: list[tuple[dict, str | None]] = []
    image_file = BytesIO()
    Image.new("RGB", (1536, 1024), color="#356276").save(image_file, format="PNG")
    image_data = image_file.getvalue()

    @upstream.post("/v1/images/generations")
    async def image_generation(request: Request) -> dict:
        """记录真实的供应商请求并返回确定性图片。"""
        body = await request.json()
        observed.append((body, request.headers.get("authorization")))
        count = body.get("sequential_image_generation_options", {}).get("max_images", 1)
        return {"data": [{"b64_json": base64.b64encode(image_data).decode("ascii")}] * count}

    cache_key = f"pytest:paintings:{uuid4().hex}"
    monkeypatch.setattr(cache, "REDIS_CACHE_KEY", cache_key)
    monkeypatch.setattr(cache.model_cache, "_local_cache", None)
    monkeypatch.setattr(cache.model_cache, "_local_cache_at", 0.0)
    monkeypatch.setenv("RUNNING_IN_DOCKER", "false")
    generate_url = "/api/paintings/generate"
    providers_url = "/api/system/model-providers"
    payload = {"model_spec": "paint-test:fixture-image", "prompt": "山间湖泊", "size": "1536x1024"}

    try:
        async with isolated_audit_store(monkeypatch) as (_, sessions), serve_http(upstream) as provider_http:
            async with sessions() as db:
                department = Department(name="绘画测试")
                db.add(department)
                await db.flush()
                users = [
                    User(
                        uid=f"painting-{role}",
                        username=f"painting-{role}",
                        role=role,
                        password_hash="unused",
                        department_id=department.id,
                    )
                    for role in ("user", "superadmin")
                ]
                db.add_all(users)
                await db.flush()
                tokens = [AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"}) for user in users]
                user_headers, admin_headers = [{"Authorization": f"Bearer {token}"} for token in tokens]
                await db.commit()

            provider = {
                "provider_id": "paint-test",
                "display_name": "绘画测试供应商",
                "provider_type": "openai",
                "base_url": f"{str(provider_http.base_url).rstrip('/')}/v1",
                "api_key": "fixture-image-key",
                "capabilities": ["image_generation", "chat", "embedding", "rerank", "video", "audio", "seed3D"],
                "enabled_models": [
                    {"id": "fixture-image", "type": "image_generation", "source": "manual"},
                    {"id": "fixture-chat", "type": "chat", "source": "manual"},
                    {"id": "fixture-video", "type": "video", "source": "manual"},
                    {"id": "fixture-audio", "type": "audio", "source": "manual"},
                    {"id": "fixture-3d", "type": "seed3D", "source": "manual"},
                ],
            }
            async with serve_http(app) as client:
                unauthorized = await client.post(generate_url, json=payload)
                assert unauthorized.status_code == 401
                assert (await client.post(providers_url, json=provider, headers=user_headers)).status_code == 403
                rejected = await client.post(providers_url, json={**provider, "provider_type": "gemini"}, headers=admin_headers)
                assert rejected.status_code == 400
                created = await client.post(providers_url, json=provider, headers=admin_headers)
                assert created.status_code == 200, created.text
                assert "fixture-image-key" not in created.text
                configured = created.json()["data"]
                assert configured["capabilities"] == ["image", "chat", "embedding", "rerank", "video", "audio", "seed3D"]
                assert [model["type"] for model in configured["enabled_models"]] == ["image", "chat", "video", "audio", "seed3D"]
                async with sessions.begin() as db:
                    stored = await db.scalar(select(ModelProvider).where(ModelProvider.provider_id == "paint-test"))
                    assert stored.capabilities == configured["capabilities"]
                    assert stored.enabled_models == configured["enabled_models"]
                    # 模拟升级前已存在数据库中的图片配置，验证真实绘画入口继续可用。
                    stored.capabilities = ["image_generation", *stored.capabilities[1:]]
                    stored.enabled_models = [{**stored.enabled_models[0], "type": "image_generation"}, *stored.enabled_models[1:]]

                refreshed = await client.post(f"{providers_url}/models/cache/refresh", headers=admin_headers)
                assert refreshed.status_code == 200, refreshed.text
                for model_type, expected_model in (
                    ("image", "fixture-image"),
                    ("image_generation", "fixture-image"),
                    ("chat", "fixture-chat"),
                    ("video", "fixture-video"),
                    ("audio", "fixture-audio"),
                    ("seed3D", "fixture-3d"),
                ):
                    directory = await client.get(f"{providers_url}/models/v2?model_type={model_type}", headers=user_headers)
                    assert directory.status_code == 200
                    assert "fixture-image-key" not in directory.text
                    catalog_models = directory.json()["data"]["paint-test"]["models"]
                    assert [item["spec"] for item in catalog_models] == [f"paint-test:{expected_model}"]
                    if expected_model == "fixture-image":
                        assert catalog_models[0]["image_aspect_ratios"] == images.IMAGE_GENERATION_ASPECT_RATIOS

                reread = await client.get(f"{providers_url}/paint-test", headers=admin_headers)
                assert reread.json()["data"]["capabilities"] == configured["capabilities"]
                assert reread.json()["data"]["enabled_models"] == configured["enabled_models"]
                status = await client.get(f"{providers_url}/models/status", params={"spec": payload["model_spec"]}, headers=admin_headers)
                assert status.status_code == 200
                assert status.json()["data"]["status"] == "unsupported"
                invalid_inputs = (
                    ({"size": "3000x3000"}, 422),
                    ({"prompt": "   "}, 422),
                    ({"base_url": "https://ignored.test/v1"}, 422),
                    ({"model_spec": "paint-test:fixture-chat"}, 503),
                )
                for extra, expected_status in invalid_inputs:
                    invalid = await client.post(generate_url, json={**payload, **extra}, headers=user_headers)
                    assert invalid.status_code == expected_status
                assert observed == []

                generated = await client.post(generate_url, json=payload, headers=user_headers)
                assert generated.status_code == 200, generated.text
                assert generated.headers["content-type"] == "image/png"
                assert generated.headers["cache-control"] == "no-store"
                assert generated.content == image_data
                with Image.open(BytesIO(generated.content)) as image:
                    assert image.size == (1536, 1024)
                    assert image.getpixel((0, 0)) == (53, 98, 118)
                assert observed == [
                    (
                        {"model": "fixture-image", "prompt": "山间湖泊", "size": "1536x1024", "n": 1, "output_format": "png"},
                        "Bearer fixture-image-key",
                    )
                ]
                render_url = "/api/paintings/render"
                invalid = await client.post(
                    render_url, json={**payload, "reference_images": ["data:image/png;base64,AA=="]}, headers=user_headers
                )
                assert invalid.status_code == 422
                assert invalid.json()["detail"]["code"] == "painting_invalid_reference"
                assert len(observed) == 1
                rendered = await client.post(render_url, json=payload, headers=user_headers)
                assert rendered.status_code == 200
                result_images = rendered.json()["images"]
                assert len(result_images) == 1 and result_images[0]["mime_type"] == "image/png"
                assert base64.b64decode(result_images[0]["data_base64"]) == image_data

                update_url = f"{providers_url}/paint-test"
                unsupported = await client.put(update_url, json={"capabilities": ["chat"]}, headers=admin_headers)
                assert unsupported.status_code == 400
                disabled = await client.put(update_url, json={"is_enabled": False}, headers=admin_headers)
                assert disabled.status_code == 200
                unavailable = await client.post(generate_url, json=payload, headers=user_headers)
                assert unavailable.status_code == 503
                assert unavailable.json()["detail"]["code"] == "painting_model_unavailable"
                assert len(observed) == 2

                ark_base_url = "https://ark.cn-beijing.volces.com/api/v3"
                ark_model = "doubao-seedream-5-5-261028"
                ark_group_model = "doubao-seedream-5-0-260128"
                # 配置仍使用正式地址，只在出站时把这一条请求发往本地测试供应商。
                monkeypatch.setattr(
                    images,
                    "get_docker_safe_url",
                    lambda url: f"{provider['base_url']}/images/generations" if url == f"{ark_base_url}/images/generations" else url,
                )
                ark_provider = {
                    **provider,
                    "provider_id": "ark-fixture",
                    "base_url": ark_base_url,
                    "capabilities": ["image"],
                    "enabled_models": [{"id": model, "type": "image", "source": "manual"} for model in (ark_model, ark_group_model, "unknown-model")],
                }
                created_ark = await client.post(providers_url, json=ark_provider, headers=admin_headers)
                assert created_ark.status_code == 200, created_ark.text
                directory = await client.get(f"{providers_url}/models/v2?model_type=image", headers=user_headers)
                assert directory.status_code == 200 and "fixture-image-key" not in directory.text
                entries = directory.json()["data"]["ark-fixture"]["models"]
                ark_entry = next(item for item in entries if item["model_id"] == ark_model)
                assert ark_entry["image_sizes"] == ["2K", "1K", "1.5K"]
                assert ark_entry["image_aspect_ratios"] == images.SEEDREAM_IMAGE_ASPECT_RATIOS
                assert ark_entry["max_reference_images"] == 3

                ark_input = {"model_spec": ark_entry["spec"], "prompt": "山间湖泊", "size": "1.5K"}
                wrong_size = await client.post(render_url, json={**ark_input, "size": "1536x1024"}, headers=user_headers)
                assert wrong_size.status_code == 422 and wrong_size.json()["detail"]["code"] == "painting_invalid_size"
                unknown = await client.post(render_url, json={**ark_input, "model_spec": "ark-fixture:unknown-model"}, headers=user_headers)
                assert unknown.status_code == 503 and unknown.json()["detail"]["code"] == "painting_model_unavailable"
                png_only = await client.post(generate_url, json=ark_input, headers=user_headers)
                assert png_only.status_code == 422 and png_only.json()["detail"]["code"] == "painting_use_render"
                assert len(observed) == 2

                reference = "data:image/png;base64," + base64.b64encode(image_data).decode("ascii")
                ark_result = await client.post(render_url, json={**ark_input, "reference_images": [reference]}, headers=user_headers)
                assert ark_result.status_code == 200, ark_result.text
                assert base64.b64decode(ark_result.json()["images"][0]["data_base64"]) == image_data
                assert observed[2:] == [
                    (
                        {
                            "model": ark_model,
                            "prompt": "山间湖泊",
                            "size": "1.5K",
                            "response_format": "b64_json",
                            "image": reference,
                        },
                        "Bearer fixture-image-key",
                    ),
                ]

                explicit_size = images.SEEDREAM_IMAGE_ASPECT_RATIOS["3:2"]
                wrong_aspect_size = await client.post(render_url, json={**ark_input, "size": "2496x1665"}, headers=user_headers)
                assert wrong_aspect_size.status_code == 422
                assert wrong_aspect_size.json()["detail"]["code"] == "painting_invalid_size"
                assert len(observed) == 3
                explicit_ratio = await client.post(render_url, json={**ark_input, "size": explicit_size}, headers=user_headers)
                assert explicit_ratio.status_code == 200, explicit_ratio.text
                assert len(observed) == 4
                assert observed[3] == (
                    {
                        "model": ark_model,
                        "prompt": "山间湖泊",
                        "size": explicit_size,
                        "response_format": "b64_json",
                    },
                    "Bearer fixture-image-key",
                )
                group_input = {"model_spec": f"ark-fixture:{ark_group_model}", "prompt": "山间湖泊", "size": "2K", "count": 2}
                group_result = await client.post(render_url, json=group_input, headers=user_headers)
                assert group_result.status_code == 200, group_result.text
                assert len(group_result.json()["images"]) == 2
                assert observed[4][0]["sequential_image_generation"] == "auto"
                assert observed[4][0]["sequential_image_generation_options"] == {"max_images": 2}
                overlimit = await client.post(render_url, json={**group_input, "count": 16}, headers=user_headers)
                assert overlimit.status_code == 422
                assert overlimit.json()["detail"]["code"] == "painting_invalid_count"
                assert len(observed) == 5
    finally:
        with sync_redis_client() as redis:
            redis.delete(cache_key)
