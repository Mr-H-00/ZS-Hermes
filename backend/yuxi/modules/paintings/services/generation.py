"""解析当前生图模型配置并执行独立绘画请求。"""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.models.images import generate_png, generate_seedream_image, generate_silicon_image, image_model_capabilities
from yuxi.modules.models.providers.ark import is_ark_url
from yuxi.modules.models.providers.credentials import resolve_api_key
from yuxi.modules.models.providers.model_types import IMAGE_MODEL_TYPE, normalize_model_type
from yuxi.modules.models.providers.repository import get_model_provider
from yuxi.shared.errors import InvalidInputError, ServiceUnavailableError


# 保持原接口的 PNG 输出，绘画工作区支持按实际格式取图。
async def generate_painting(db: AsyncSession, *, model_spec: str, prompt: str, size: str) -> bytes:
    """校验用户输入，读取已启用的生图模型，然后向供应商生成一张图。"""
    images = await render_painting(db, model_spec=model_spec, prompt=prompt, size=size, count=1, png_only=True)
    mime_type, content = images[0]
    if mime_type != "image/png":
        raise InvalidInputError("此模型的输出请使用绘画工作区获取", code="painting_use_render")
    return content


# 按当前供应商能力校验输入并选择生图协议。
async def render_painting(
    db: AsyncSession,
    *,
    model_spec: str,
    prompt: str,
    size: str,
    count: int,
    reference_images: list[str] | None = None,
    parameters: dict[str, str | int | float] | None = None,
    png_only: bool = False,
) -> list[tuple[str, bytes]]:
    """以当前模型能力校验尺寸、参考图与参数后生成图片。"""
    prompt = prompt.strip()
    if not prompt or len(prompt) > 4000:
        raise InvalidInputError("请输入 1–4000 个字符的绘画提示词", code="painting_invalid_prompt")
    provider_id, _, model_id = model_spec.partition(":")
    if not provider_id or not model_id or len(model_spec) > 500:
        raise InvalidInputError("请选择可用的生图模型", code="painting_invalid_model")

    provider = await get_model_provider(db, provider_id)
    if (
        not provider
        or not provider.is_enabled
        or provider.provider_type != "openai"
        or not any(normalize_model_type(capability) == IMAGE_MODEL_TYPE for capability in cast(list[str], provider.capabilities or []))
    ):
        raise ServiceUnavailableError("生图模型不可用，请重新选择或联系管理员", code="painting_model_unavailable")
    enabled_models = cast(list[dict[str, Any]], provider.enabled_models or [])
    model = next(
        (item for item in enabled_models if item.get("id") == model_id and normalize_model_type(item.get("type", "")) == IMAGE_MODEL_TYPE),
        None,
    )
    if model is None:
        raise ServiceUnavailableError("生图模型不可用，请重新选择或联系管理员", code="painting_model_unavailable")
    api_key = (resolve_api_key(provider) or "").strip()
    if not api_key:
        raise ServiceUnavailableError("生图供应商未配置凭据，请联系管理员", code="painting_provider_unavailable")
    base_url = model.get("base_url_override") or str(provider.base_url)
    if not isinstance(base_url, str):
        raise ServiceUnavailableError("生图供应商配置无效", code="painting_provider_unavailable")
    use_seedream = is_ark_url(base_url)
    if use_seedream and png_only:
        raise InvalidInputError("此模型的输出请使用绘画工作区获取", code="painting_use_render")
    capabilities = image_model_capabilities(base_url, model_id)
    if not capabilities["image_sizes"]:
        raise ServiceUnavailableError("当前绘画页暂不支持此生图模型，请重新选择", code="painting_model_unavailable")
    if size not in capabilities["image_sizes"] and size not in capabilities["image_aspect_ratios"].values():
        raise InvalidInputError("请选择支持的图片尺寸", code="painting_invalid_size")
    if count < 1 or count > capabilities["max_images"]:
        raise InvalidInputError("生成数量超出所选模型限制", code="painting_invalid_count")
    references = reference_images or []
    if len(references) > capabilities["max_reference_images"] or (not references and not capabilities["accepts_prompt_only"]):
        raise InvalidInputError("参考图数量不符合所选模型的要求", code="painting_invalid_reference")
    supplied_parameters = parameters or {}
    if supplied_parameters.keys() - set(capabilities["image_parameters"]):
        raise InvalidInputError("所选模型不支持此生成参数", code="painting_invalid_parameter")
    headers = dict(provider.headers_json or {})
    # 供应商生成可能耗时较长。配置已经读入，释放本请求的只读数据库事务。
    await db.commit()
    if use_seedream:
        return await generate_seedream_image(
            base_url=base_url,
            headers=headers,
            api_key=api_key,
            model_id=model_id,
            prompt=prompt,
            size=size,
            reference_images=references,
            count=count,
        )
    if capabilities["image_parameters"] or capabilities["max_reference_images"]:
        return [
            await generate_silicon_image(
                base_url=base_url,
                headers=headers,
                api_key=api_key,
                model_id=model_id,
                prompt=prompt,
                size=size,
                reference_images=references,
                parameters=supplied_parameters,
            )
        ]
    return [
        ("image/png", await generate_png(base_url=base_url, headers=headers, api_key=api_key, model_id=model_id, prompt=prompt, size=size))
    ]
