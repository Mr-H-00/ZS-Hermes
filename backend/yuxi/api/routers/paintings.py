"""独立绘画页面的认证 HTTP 接口。"""

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.identity.models import User
from yuxi.modules.paintings.services.generation import generate_painting, render_painting

router = APIRouter(prefix="/paintings", tags=["paintings"])


class PaintingInput(BaseModel):
    """客户端只能提交模型标识、提示词和尺寸。"""

    model_config = ConfigDict(extra="forbid")

    model_spec: str
    prompt: str
    size: str


class PaintingRenderInput(PaintingInput):
    """接收绘画工作区的模型参数，以及浏览器上传的限量参考图。"""

    count: int = 1
    reference_images: list[Annotated[str, Field(max_length=4 * 1024 * 1024 + 32)]] = Field(default_factory=list, max_length=3)
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    guidance_scale: float | None = Field(default=None, ge=1, le=20)


class RenderedImage(BaseModel):
    """已验证的图片格式与正文。"""

    mime_type: str
    data_base64: str


class PaintingRenderResult(BaseModel):
    """绘画工作区的结果列表。"""

    images: list[RenderedImage]


@router.post("/generate", response_class=Response, responses={200: {"content": {"image/png": {}}}})
async def generate_painting_route(
    payload: PaintingInput,
    request: Request,
    _user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """只对 Web 登录用户返回已验证的 PNG 字节。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="绘画仅支持 Web 登录用户")
    content = await generate_painting(db, model_spec=payload.model_spec, prompt=payload.prompt, size=payload.size)
    return Response(content, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/render", response_model=PaintingRenderResult)
async def render_painting_route(
    payload: PaintingRenderInput,
    request: Request,
    response: Response,
    _user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
) -> PaintingRenderResult:
    """调用已启用的生图模型并以校验过的字节展示结果。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="绘画仅支持 Web 登录用户")
    parameters = payload.model_dump(include={"seed", "negative_prompt", "guidance_scale"}, exclude_none=True)
    images = await render_painting(
        db,
        model_spec=payload.model_spec,
        prompt=payload.prompt,
        size=payload.size,
        count=payload.count,
        reference_images=payload.reference_images,
        parameters=parameters,
    )
    response.headers["Cache-Control"] = "no-store"
    return PaintingRenderResult(
        images=[RenderedImage(mime_type=mime_type, data_base64=base64.b64encode(image).decode("ascii")) for mime_type, image in images]
    )
