"""验证不合规图像响应和生成超时的适配器边界。"""

import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pytest_httpx import HTTPXMock

from yuxi.modules.models import images
from yuxi.modules.models.utils import get_docker_safe_url
from yuxi.shared.errors import InvalidInputError, OperationTimeoutError, UpstreamError

_BASE_URL = "https://image-provider.test/v1"
_ENDPOINT = f"{_BASE_URL}/images/generations"
_SILICON_ENDPOINT = "https://api.siliconflow.cn/v1/images/generations"
_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_ARK_ENDPOINT = f"{_ARK_BASE_URL}/images/generations"


async def _generate_image() -> bytes:
    """向固定测试端点请求一张方形图片。"""
    return await images.generate_png(
        base_url=_BASE_URL, headers={}, api_key="test-only-key", model_id="test-image", prompt="湖泊", size="1024x1024"
    )


@pytest.mark.parametrize(
    "bad_image",
    [
        {"url": "https://untrusted.test/image.png"},
        {"b64_json": "$$"},
        {"b64_json": "aGVsbG8="},
    ],
)
async def test_generation_rejects_url_invalid_base64_and_non_image(httpx_mock: HTTPXMock, bad_image: dict) -> None:
    """测试配额内的畸形响应不会被当作图片，也不会触发 URL 下载。"""
    httpx_mock.add_response(url=_ENDPOINT, method="POST", json={"data": [bad_image]})
    with pytest.raises(UpstreamError) as error:
        await _generate_image()
    assert error.value.code == "painting_invalid_response"


async def test_generation_rejects_oversized_response(httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """超出响应字节预算时在解析之前失败。"""
    monkeypatch.setattr(images, "_MAX_RESPONSE_BYTES", 128)
    httpx_mock.add_response(url=_ENDPOINT, method="POST", content=b"x" * 129)
    with pytest.raises(UpstreamError, match="响应过大"):
        await _generate_image()


async def test_generation_translates_upstream_timeout(httpx_mock: HTTPXMock) -> None:
    """超时通过专用错误返回且无需重发上游请求。"""
    httpx_mock.add_exception(httpx.ReadTimeout("test timeout"), url=_ENDPOINT, method="POST")
    with pytest.raises(OperationTimeoutError) as error:
        await _generate_image()
    assert error.value.code == "painting_timeout"


async def test_generation_uses_host_gateway_for_container_loopback(httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """在 Compose 环境把宿主机 HTTP 模型请求发送到既有网关。"""
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
    httpx_mock.add_exception(httpx.ReadTimeout("test timeout"), url="http://host.docker.internal:9000/v1/images/generations")
    with pytest.raises(OperationTimeoutError):
        await images.generate_png(
            base_url="http://127.0.0.1:9000/v1", headers={}, api_key="test-key", model_id="test-image", prompt="湖泊", size="1024x1024"
        )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("http://localhost:9000/v1", "http://host.docker.internal:9000/v1"),
        ("http://localhost.example/v1", "http://localhost.example/v1"),
        ("http://localhost:9000@upstream.example/v1", "http://localhost:9000@upstream.example/v1"),
        ("https://upstream.example/path/http://127.0.0.1", "https://upstream.example/path/http://127.0.0.1"),
    ],
)
def test_docker_model_url_rewrites_only_the_loopback_host(monkeypatch: pytest.MonkeyPatch, original: str, expected: str) -> None:
    """不重写前缀相同的外部域名、凭据或路径。"""
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
    assert get_docker_safe_url(original) == expected


async def test_qwen_reads_only_provider_object_host_without_bearer(httpx_mock: HTTPXMock) -> None:
    """硅基模型请求携带正确参数，图片下载使用签名地址而不携带模型凭据。"""
    image_url = "https://s3.siliconflow.cn/generated/test.webp?signature=fixture"
    image_file = BytesIO()
    Image.new("RGB", (12, 8), "red").save(image_file, format="WEBP")
    image_bytes = image_file.getvalue()
    httpx_mock.add_response(
        method="POST",
        url=_SILICON_ENDPOINT,
        match_headers={"Authorization": "Bearer test-qwen-key"},
        match_json={"model": "Qwen/Qwen-Image", "prompt": "湖泊", "image_size": "1664x928", "seed": 1},
        json={"images": [{"url": image_url}]},
    )
    httpx_mock.add_response(method="GET", url=image_url, content=image_bytes)
    mime_type, result = await images.generate_silicon_image(
        base_url="https://api.siliconflow.cn/v1",
        headers={"X-Example": "secret"},
        api_key="test-qwen-key",
        model_id="Qwen/Qwen-Image",
        prompt="湖泊",
        size="1664x928",
        reference_images=[],
        parameters={"seed": 1},
    )
    assert mime_type == "image/webp"
    assert result == image_bytes
    download = httpx_mock.get_request(method="GET")
    assert download is not None and "Authorization" not in download.headers and "X-Example" not in download.headers


@pytest.mark.parametrize("unsafe_url", ["http://s3.siliconflow.cn/image.png", "https://s3.siliconflow.cn.other.test/image.png"])
async def test_qwen_rejects_non_provider_url(httpx_mock: HTTPXMock, unsafe_url: str) -> None:
    """不能根据上游响应请求非供应商地址或非 HTTPS 对象。"""
    httpx_mock.add_response(method="POST", url=_SILICON_ENDPOINT, json={"images": [{"url": unsafe_url}]})
    with pytest.raises(UpstreamError, match="未返回有效的图片"):
        await images.generate_silicon_image(
            base_url="https://api.siliconflow.cn/v1",
            headers={},
            api_key="test-qwen-key",
            model_id="Qwen/Qwen-Image",
            prompt="湖泊",
            size="1664x928",
            reference_images=[],
            parameters={},
        )


@pytest.mark.parametrize(
    ("model_id", "sizes"),
    [
        ("doubao-seedream-5-5-261028", ["2K", "1K", "1.5K"]),
        ("doubao-seedream-5-0-pro-260628", ["2K", "1K", "1.5K"]),
        ("doubao-seedream-5-0-flash-260915", ["2K", "1K", "1.5K"]),
        ("doubao-seedream-5-0-260128", ["2K", "3K", "4K"]),
        ("doubao-seedream-5-0-lite-260128", ["2K", "3K", "4K"]),
        ("doubao-seedream-4-5-251128", ["2K", "4K"]),
        ("doubao-seedream-4-0-20260415", ["2K", "1K", "4K"]),
        ("unverified-model", []),
    ],
)
# 核对已验证的方舟型号对应的分辨率和参考图限制。
def test_seedream_dimensions_and_reference_capabilities(model_id: str, sizes: list[str]) -> None:
    """型号能力用于目录和执行校验，未知型号不暴露 GPT Image 选项。"""
    capabilities = images.image_model_capabilities(_ARK_BASE_URL, model_id)
    assert capabilities["image_sizes"] == sizes
    assert capabilities["image_aspect_ratios"] == (images.SEEDREAM_IMAGE_ASPECT_RATIOS if sizes else {})
    assert capabilities["max_images"] == (15 if model_id in {
        "doubao-seedream-5-0-260128",
        "doubao-seedream-5-0-lite-260128",
        "doubao-seedream-4-5-251128",
        "doubao-seedream-4-0-20260415",
    } else 1)
    assert capabilities["max_reference_images"] == (3 if sizes else 0)
    assert capabilities["accepts_prompt_only"] is bool(sizes)
    assert images.image_model_capabilities(_BASE_URL, model_id)["image_sizes"] == list(images.IMAGE_GENERATION_SIZES)


# 检查比例尺寸不会超出 Seedream 型号支持的像素范围。
def test_seedream_aspect_ratio_dimensions_fit_official_pixel_limits() -> None:
    """方舟比例选项使用有效的像素尺寸并保留指定比例。"""
    for aspect_ratio, size in images.SEEDREAM_IMAGE_ASPECT_RATIOS.items():
        if aspect_ratio == "auto":
            continue
        width, height = map(int, size.split("x"))
        expected_width, expected_height = map(int, aspect_ratio.split(":"))
        assert abs(width / height - expected_width / expected_height) < 0.001
        pixels = width * height
        assert 3_686_400 <= pixels <= 4_624_220


# 核对静态模型只公开其确实支持的固定宽高比。
@pytest.mark.parametrize(
    ("base_url", "model_id", "expected"),
    [
        (_BASE_URL, "gpt-image-1", images.IMAGE_GENERATION_ASPECT_RATIOS),
        ("https://api.siliconflow.cn/v1", "Qwen/Qwen-Image", images.QWEN_IMAGE_ASPECT_RATIOS),
        (_BASE_URL, "unknown-model", images.IMAGE_GENERATION_ASPECT_RATIOS),
    ],
)
def test_fixed_image_aspect_ratio_dimensions(base_url: str, model_id: str, expected: dict[str, str]) -> None:
    """GPT 和 Qwen 比例只映射到目录提供的具体尺寸。"""
    assert images.image_model_capabilities(base_url, model_id)["image_aspect_ratios"] == expected


# 验证 Lite 多参考图请求和高清图片的实际返回值。
async def test_seedream_lite_requests_single_output_with_references(httpx_mock: HTTPXMock) -> None:
    """方舟需要分辨率档位和 image 数组，4K 档位可返回长边大于 4096 的图片。"""
    reference_file = BytesIO()
    Image.new("RGB", (32, 24)).save(reference_file, format="PNG")
    reference = "data:image/png;base64," + base64.b64encode(reference_file.getvalue()).decode()
    output_file = BytesIO()
    Image.new("RGB", (5120, 2880)).save(output_file, format="JPEG")
    content = output_file.getvalue()
    model_id = "doubao-seedream-5-0-260128"
    httpx_mock.add_response(
        method="POST",
        url=_ARK_ENDPOINT,
        match_headers={"Authorization": "Bearer fixture"},
        match_json={
            "model": model_id,
            "prompt": "山间湖泊",
            "size": "4K",
            "response_format": "b64_json",
            "image": [reference, reference],
            "sequential_image_generation": "disabled",
        },
        json={"data": [{"b64_json": base64.b64encode(content).decode()}]},
    )
    assert await images.generate_seedream_image(
        base_url=_ARK_BASE_URL,
        headers={},
        api_key="fixture",
        model_id=model_id,
        prompt="山间湖泊",
        size="4K",
        reference_images=[reference, reference],
    ) == [("image/jpeg", content)]


# 验证 Seedream 组图请求和结果数量按模型能力贯穿。
async def test_seedream_group_requests_and_decodes_requested_count(httpx_mock: HTTPXMock) -> None:
    """组图请求提交数量上限，并逐张校验返回图片。"""
    output_file = BytesIO()
    Image.new("RGB", (32, 24), color="red").save(output_file, format="JPEG")
    content = output_file.getvalue()
    model_id = "doubao-seedream-5-0-260128"
    httpx_mock.add_response(
        method="POST",
        url=_ARK_ENDPOINT,
        match_json={
            "model": model_id,
            "prompt": "山间湖泊",
            "size": "2K",
            "response_format": "b64_json",
            "sequential_image_generation": "auto",
            "sequential_image_generation_options": {"max_images": 2},
        },
        json={"data": [{"b64_json": base64.b64encode(content).decode()}] * 2},
    )
    assert await images.generate_seedream_image(
        base_url=_ARK_BASE_URL,
        headers={},
        api_key="fixture",
        model_id=model_id,
        prompt="山间湖泊",
        size="2K",
        reference_images=[],
        count=2,
    ) == [("image/jpeg", content), ("image/jpeg", content)]


# 验证参考图只接受本地上传字节，返回地址不触发第二次请求。
async def test_seedream_rejects_input_and_output_urls(httpx_mock: HTTPXMock) -> None:
    """输入拒绝地址，成功响应缺少 base64 图片时也失败。"""
    request = {
        "base_url": _ARK_BASE_URL,
        "headers": {},
        "api_key": "fixture",
        "model_id": "doubao-seedream-5-5-261028",
        "prompt": "山间湖泊",
        "size": "2K",
    }
    with pytest.raises(InvalidInputError) as error:
        await images.generate_seedream_image(**request, reference_images=["https://example.com/fixture.png"])
    assert error.value.code == "painting_invalid_reference"
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(method="POST", url=_ARK_ENDPOINT, json={"data": [{"url": "https://example.com/fixture.png"}]})
    with pytest.raises(UpstreamError) as response_error:
        await images.generate_seedream_image(**request, reference_images=[])
    assert response_error.value.code == "painting_invalid_response"
