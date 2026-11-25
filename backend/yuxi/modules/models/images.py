"""调用生图供应商端点，校验返回的图片。"""

import asyncio
import base64
import json
from io import BytesIO
from typing import Any, TypedDict

import httpx
from PIL import Image

from yuxi.modules.models.providers.ark import is_ark_url
from yuxi.modules.models.utils import get_docker_safe_url
from yuxi.shared.errors import InvalidInputError, OperationTimeoutError, ServiceUnavailableError, UpstreamError

IMAGE_GENERATION_SIZES = ("1024x1024", "1536x1024", "1024x1536")
QWEN_IMAGE_SIZES = ("1664x928", "928x1664", "1328x1328", "1472x1140", "1140x1472")
IMAGE_GENERATION_ASPECT_RATIOS = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
}
QWEN_IMAGE_ASPECT_RATIOS = {
    "16:9": "1664x928",
    "9:16": "928x1664",
    "1:1": "1328x1328",
    "4:3": "1472x1140",
    "3:4": "1140x1472",
}
SEEDREAM_IMAGE_ASPECT_RATIOS = {
    "auto": "2K",
    "9:16": "1440x2560",
    "2:3": "1664x2496",
    "3:4": "1728x2304",
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:2": "2496x1664",
    "16:9": "2560x1440",
}
_QWEN_IMAGE = "Qwen/Qwen-Image"
_QWEN_IMAGE_EDIT = "Qwen/Qwen-Image-Edit-2509"
_SILICON_IMAGE_HOSTS = {"api.siliconflow.cn": "s3.siliconflow.cn", "api.siliconflow.com": "s3.siliconflow.com"}
_SEEDREAM_SINGLE_MODELS = {
    "doubao-seedream-5-5-261028",
    "doubao-seedream-5-0-pro-260628",
    "doubao-seedream-5-0-flash-260915",
}
_SEEDREAM_SINGLE_SIZES = ("2K", "1K", "1.5K")
_SEEDREAM_GROUP_MODEL_SIZES = {
    "doubao-seedream-5-0-260128": ("2K", "3K", "4K"),
    "doubao-seedream-5-0-lite-260128": ("2K", "3K", "4K"),
    "doubao-seedream-4-5-251128": ("2K", "4K"),
    "doubao-seedream-4-0-20260415": ("2K", "1K", "4K"),
}
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_RESPONSE_BYTES = 17 * 1024 * 1024
_MAX_BATCH_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_REFERENCE_BYTES = 3 * 1024 * 1024
_TIMEOUT_SECONDS = 120
_MAX_SEEDREAM_IMAGES = 15
_MAX_TOTAL_IMAGE_BYTES = 48 * 1024 * 1024


class ImageModelCapabilities(TypedDict):
    """绘画目录与执行层共同使用的模型能力。"""

    image_sizes: list[str]
    image_aspect_ratios: dict[str, str]
    max_images: int
    max_reference_images: int
    accepts_prompt_only: bool
    image_parameters: list[str]


# 从同一模型能力读取可展示和可提交的尺寸及参考图限制。
def image_model_capabilities(base_url: str, model_id: str) -> ImageModelCapabilities:
    """按上游端点与已启用的模型返回绘画表单能力。"""
    if is_ark_url(base_url):
        sizes = _SEEDREAM_SINGLE_SIZES if model_id in _SEEDREAM_SINGLE_MODELS else _SEEDREAM_GROUP_MODEL_SIZES.get(model_id, ())
        return {
            "image_sizes": list(sizes),
            "image_aspect_ratios": dict(SEEDREAM_IMAGE_ASPECT_RATIOS) if sizes else {},
            "max_images": _MAX_SEEDREAM_IMAGES if model_id in _SEEDREAM_GROUP_MODEL_SIZES else 1,
            "max_reference_images": 3 if sizes else 0,
            "accepts_prompt_only": bool(sizes),
            "image_parameters": [],
        }
    silicon_model = _silicon_image_host(base_url, model_id)
    image_sizes = list(QWEN_IMAGE_SIZES if silicon_model else IMAGE_GENERATION_SIZES)
    return {
        "image_sizes": image_sizes,
        "image_aspect_ratios": dict(QWEN_IMAGE_ASPECT_RATIOS if silicon_model else IMAGE_GENERATION_ASPECT_RATIOS),
        "max_images": 1,
        "max_reference_images": 3 if silicon_model and model_id == _QWEN_IMAGE_EDIT else 0,
        "accepts_prompt_only": not silicon_model or model_id != _QWEN_IMAGE_EDIT,
        "image_parameters": ["seed", "negative_prompt", "guidance_scale"] if silicon_model and model_id == _QWEN_IMAGE else [],
    }


async def generate_png(*, base_url: str, headers: dict[str, str], api_key: str, model_id: str, prompt: str, size: str) -> bytes:
    """向受管理的供应商请求一张指定尺寸的 PNG，返回已验证的图片字节。"""
    endpoint, request_headers = _generation_request(base_url, headers, api_key)
    payload = {"model": model_id, "prompt": prompt, "size": size, "n": 1, "output_format": "png"}
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            body = await _read_response(client, "POST", endpoint, _MAX_RESPONSE_BYTES, headers=request_headers, json=payload)
    except (TimeoutError, httpx.TimeoutException):
        raise OperationTimeoutError("图片生成超时，请稍后重试", code="painting_timeout") from None
    return await asyncio.to_thread(_decode_png, body, size)


# 将方舟模型的分辨率档位和参考图提交到同一个生成接口。
async def generate_seedream_image(
    *,
    base_url: str,
    headers: dict[str, str],
    api_key: str,
    model_id: str,
    prompt: str,
    size: str,
    reference_images: list[str],
    count: int = 1,
) -> list[tuple[str, bytes]]:
    """向方舟请求有限数量的 Seedream 图片并校验每张结果。"""
    endpoint, request_headers = _generation_request(base_url, headers, api_key)
    for reference in reference_images:
        await asyncio.to_thread(_validate_reference_image, reference)
    payload: dict[str, Any] = {
        "model": model_id,
        "prompt": prompt,
        "size": size,
        "response_format": "b64_json",
    }
    if reference_images:
        payload["image"] = reference_images[0] if len(reference_images) == 1 else reference_images
    if model_id in _SEEDREAM_GROUP_MODEL_SIZES:
        payload["sequential_image_generation"] = "auto" if count > 1 else "disabled"
        if count > 1:
            payload["sequential_image_generation_options"] = {"max_images": count}
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response_budget = _MAX_BATCH_RESPONSE_BYTES if count > 1 else _MAX_RESPONSE_BYTES
            body = await _read_response(client, "POST", endpoint, response_budget, headers=request_headers, json=payload)
    except (TimeoutError, httpx.TimeoutException):
        raise OperationTimeoutError("图片生成超时，请稍后重试", code="painting_timeout") from None
    return await asyncio.to_thread(_decode_seedream, body, count)


async def generate_silicon_image(
    *,
    base_url: str,
    headers: dict[str, str],
    api_key: str,
    model_id: str,
    prompt: str,
    size: str,
    reference_images: list[str],
    parameters: dict[str, str | int | float],
) -> tuple[str, bytes]:
    """调用硅基流动的 Qwen Images 接口并验证签名地址返回的图像。"""
    image_host = _silicon_image_host(base_url, model_id)
    if not image_host:
        raise ServiceUnavailableError("生图供应商配置无效", code="painting_provider_unavailable")
    endpoint, request_headers = _generation_request(base_url, headers, api_key)
    for reference in reference_images:
        await asyncio.to_thread(_validate_reference_image, reference)
    payload = {"model": model_id, "prompt": prompt, "image_size": size, **parameters}
    payload.update(dict(zip(("image", "image2", "image3"), reference_images)))
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS), httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            body = await _read_response(client, "POST", endpoint, _MAX_RESPONSE_BYTES, headers=request_headers, json=payload)
            image = _silicon_response_image(body)
            if encoded := image.get("b64_json"):
                content = _decode_base64(encoded, _MAX_IMAGE_BYTES)
            else:
                url = httpx.URL(image.get("url") or "")
                # 供应商签名 URL 只允许固定对象服务，且读取时不发送模型凭据。
                if url.scheme != "https" or url.host != image_host or url.port not in (443, None) or url.userinfo:
                    raise ValueError("invalid image URL")
                content = bytes(await _read_response(client, "GET", url, _MAX_IMAGE_BYTES, headers={"Accept-Encoding": "identity"}))
            mime_type = await asyncio.to_thread(_inspect_image, content)
            return mime_type, content
    except (TimeoutError, httpx.TimeoutException):
        raise OperationTimeoutError("图片生成超时，请稍后重试", code="painting_timeout") from None
    except (KeyError, TypeError, ValueError, httpx.InvalidURL, OSError, Image.DecompressionBombError, SyntaxError, RecursionError):
        raise UpstreamError("生图供应商未返回有效的图片", code="painting_invalid_response") from None


def _silicon_image_host(base_url: str, model_id: str) -> str | None:
    """只承认官方硅基流动端点的 Qwen 生成和编辑协议。"""
    if model_id not in {_QWEN_IMAGE, _QWEN_IMAGE_EDIT}:
        return None
    try:
        url = httpx.URL(base_url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    if url.scheme != "https" or url.port not in (443, None):
        return None
    return _SILICON_IMAGE_HOSTS.get(url.host)


def _generation_request(base_url: str, headers: dict[str, str], api_key: str) -> tuple[httpx.URL, httpx.Headers]:
    """使用管理员配置装配模型 URL 与服务端认证。"""
    try:
        endpoint = httpx.URL(base_url.rstrip("/") + "/images/generations")
        if endpoint.scheme not in {"http", "https"} or not endpoint.host or endpoint.userinfo or endpoint.query or endpoint.fragment:
            raise ValueError("invalid endpoint")
        endpoint = httpx.URL(get_docker_safe_url(str(endpoint)))
        request_headers = httpx.Headers(headers)
        request_headers["Authorization"] = f"Bearer {api_key}"
        request_headers["Accept-Encoding"] = "identity"
    except (httpx.InvalidURL, TypeError, ValueError):
        raise ServiceUnavailableError("生图供应商配置无效", code="painting_provider_unavailable") from None

    return endpoint, request_headers


async def _read_response(client: httpx.AsyncClient, method: str, url: httpx.URL, budget: int, **kwargs: Any) -> bytearray:
    """按字节预算读取上游，拒绝压缩和重定向响应。"""
    try:
        async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise UpstreamError("生图供应商返回了不支持的响应编码", code="painting_invalid_response")
            body = bytearray()
            async for chunk in response.aiter_raw(chunk_size=65536):
                if len(body) + len(chunk) > budget:
                    raise UpstreamError("生图供应商响应过大", code="painting_invalid_response")
                body.extend(chunk)
            return body
    except httpx.TimeoutException:
        raise
    except httpx.HTTPStatusError as exc:
        message = "生图供应商限流或额度不足，请稍后重试" if exc.response.status_code == 429 else "生图供应商拒绝生成，请检查模型配置"
        raise UpstreamError(message, code="painting_provider_rejected") from None
    except httpx.RequestError:
        raise UpstreamError("生图供应商连接失败，请稍后重试", code="painting_provider_unreachable") from None


def _silicon_response_image(body: bytearray) -> dict[str, Any]:
    """读取单张 Qwen 结果及其图片位置。"""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("invalid image response")
    entries = payload.get("images", payload.get("data"))
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("expected one image")
    return entries[0]


def _decode_base64(encoded: Any, budget: int) -> bytes:
    """只接受限量的合法 Base64 字节。"""
    if not isinstance(encoded, str) or len(encoded) > ((budget + 2) // 3) * 4:
        raise ValueError("image exceeds size limit")
    content = base64.b64decode(encoded, validate=True)
    if not content or len(content) > budget:
        raise ValueError("invalid image length")
    return content


# 核对像素预算和图片格式，并实际解码。
def _inspect_image(content: bytes, *, size: tuple[int, int] | None = None, max_pixels: int | None = None) -> str:
    """完整解码并确认 MIME、尺寸与帧数。"""
    formats = ["PNG"] if size else ["PNG", "JPEG", "WEBP"]
    with Image.open(BytesIO(content), formats=formats) as image:
        over_budget = image.width * image.height > max_pixels if max_pixels is not None else max(image.size) > 4096
        if (size and image.size != size) or over_budget or getattr(image, "is_animated", False):
            raise ValueError("unexpected image dimensions or frames")
        image.load()
        return Image.MIME[image.format or ""]


def _validate_reference_image(reference: str) -> None:
    """校验参考图的 data URL、字节大小和实际内容。"""
    header, _, value = reference.partition(",")
    try:
        mime_type = _inspect_image(_decode_base64(value, _MAX_REFERENCE_BYTES))
        if header != f"data:{mime_type};base64":
            raise ValueError("invalid reference MIME")
    except (KeyError, ValueError, OSError, Image.DecompressionBombError, SyntaxError):
        raise InvalidInputError("参考图必须是 3 MB 以内的 PNG、JPEG 或 WebP 图片", code="painting_invalid_reference") from None


def _decode_png(body: bytearray, size: str) -> bytes:
    """拒绝 URL、无效 Base64、超限或无法解码的图片。"""
    try:
        entries = json.loads(body)["data"]
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("expected one image")
        content = _decode_base64(entries[0]["b64_json"], _MAX_IMAGE_BYTES)
        width, height = map(int, size.split("x"))
        _inspect_image(content, size=(width, height))
    except (KeyError, TypeError, ValueError, OSError, Image.DecompressionBombError, SyntaxError, RecursionError):
        raise UpstreamError("生图供应商未返回有效的 PNG 图片", code="painting_invalid_response") from None
    return content


# 检查方舟响应中的实际像素，分辨率档位允许模型按提示词决定宽高比。
def _decode_seedream(body: bytearray, expected_count: int = 1) -> list[tuple[str, bytes]]:
    """读取有限数量的 base64 图片，验证格式、像素和总字节预算。"""
    try:
        entries = json.loads(body)["data"]
        if not isinstance(entries, list) or len(entries) != expected_count:
            raise ValueError("expected one image")
        results = []
        total = 0
        for entry in entries:
            content = _decode_base64(entry["b64_json"], _MAX_IMAGE_BYTES)
            total += len(content)
            if total > _MAX_TOTAL_IMAGE_BYTES:
                raise ValueError("images exceed total size limit")
            results.append((_inspect_image(content, max_pixels=4096**2), content))
        return results
    except (KeyError, TypeError, ValueError, OSError, Image.DecompressionBombError, SyntaxError, RecursionError):
        raise UpstreamError("生图供应商未返回有效的图片", code="painting_invalid_response") from None
