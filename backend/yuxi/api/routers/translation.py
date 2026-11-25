"""提供登录用户的翻译、文件提取和语言检测接口。"""

import asyncio
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.identity.models import User
from yuxi.modules.translation.extraction import MAX_UPLOAD_BYTES, extract_translation_text
from yuxi.modules.translation.pdf import MAX_PDF_UPLOAD_BYTES, pdf_translation_available, translate_pdf
from yuxi.modules.translation.services import TranslationOptions, detect_language, translate_text
from yuxi.shared.errors import ContentTooLargeError, InvalidInputError

router = APIRouter(tags=["translation"])


async def require_translation_user(request: Request, user: User = Depends(get_required_user)) -> User:
    """翻译相关操作只接受产品登录身份。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="翻译仅支持 Web 登录用户")
    return user


class TranslationInput(BaseModel):
    """翻译请求的原文、语言和模型 spec。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    source_language: str
    target_language: str
    model_spec: str
    source_language_name: str | None = Field(default=None, max_length=80)
    target_language_name: str | None = Field(default=None, max_length=80)
    prompt: str | None = Field(default=None, max_length=8_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None


class TranslationResult(BaseModel):
    """独立翻译的纯文本结果。"""

    translated_text: str


class LanguageChoice(BaseModel):
    """语言检测的一项候选代码和名称。"""

    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)


class LanguageDetectionInput(BaseModel):
    """当前模型、待检测文本与有限的候选集。"""

    model_config = ConfigDict(extra="forbid")
    text: str
    model_spec: str
    languages: list[LanguageChoice] = Field(min_length=1, max_length=100)


class LanguageDetectionResult(BaseModel):
    """无法识别的原文语言返回空值。"""

    source_language: str | None


class ExtractionResult(BaseModel):
    """已提取、可编辑的正文。"""

    text: str


class PdfCapability(BaseModel):
    """部署是否装有 PDF 保版式翻译引擎。"""

    available: bool


@router.post("/translate", response_model=TranslationResult)
async def translate(
    payload: TranslationInput,
    response: Response,
    user: User = Depends(require_translation_user),
    db: AsyncSession = Depends(get_db),
) -> TranslationResult:
    """只允许 Web 登录身份执行翻译，并禁止缓存结果。"""
    translated = await translate_text(
        db,
        uid=str(user.uid),
        text=payload.text,
        source_language=payload.source_language,
        target_language=payload.target_language,
        model_spec=payload.model_spec,
        options=TranslationOptions(
            source_language_name=payload.source_language_name,
            target_language_name=payload.target_language_name,
            prompt=payload.prompt,
            temperature=payload.temperature,
            top_p=payload.top_p,
            reasoning_effort=payload.reasoning_effort,
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return TranslationResult(translated_text=translated)


@router.post("/translate/detect", response_model=LanguageDetectionResult)
async def detect(
    payload: LanguageDetectionInput,
    response: Response,
    user: User = Depends(require_translation_user),
    db: AsyncSession = Depends(get_db),
) -> LanguageDetectionResult:
    """使用已配置的聊天模型识别本次原文的语言。"""
    language_names = {language.code: language.name for language in payload.languages}
    if len({code.casefold() for code in language_names}) != len(payload.languages):
        raise InvalidInputError("语言列表包含重复代码", code="translation_language_duplicate")
    code = await detect_language(db, uid=str(user.uid), model_spec=payload.model_spec, text=payload.text, languages=language_names)
    response.headers["Cache-Control"] = "no-store"
    return LanguageDetectionResult(source_language=code)


@router.post("/translate/extract", response_model=ExtractionResult, dependencies=[Depends(require_translation_user)])
async def extract(response: Response, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)) -> ExtractionResult:
    """解析单份 HTTP 上传，返回正文并禁止缓存。"""
    too_large = "上传文件不能超过 10 MiB"
    try:
        content = await read_upload_with_limit(file, max_size_bytes=MAX_UPLOAD_BYTES, too_large_message=too_large)
    except ValueError as exc:
        raise ContentTooLargeError(too_large, code="translation_file_too_large") from exc
    text = await extract_translation_text(db, filename=file.filename or "", content=content)
    response.headers["Cache-Control"] = "no-store"
    return ExtractionResult(text=text)


@router.get("/translate/pdf/capability", response_model=PdfCapability, dependencies=[Depends(require_translation_user)])
async def pdf_capability(response: Response) -> PdfCapability:
    """报告 PDF 版式能力，供页面选择可用操作。"""
    response.headers["Cache-Control"] = "no-store"
    return PdfCapability(available=pdf_translation_available())


@router.post("/translate/pdf", response_class=Response)
async def pdf(
    request: Request,
    file: UploadFile = File(...),
    target_language: str = Form(...),
    model_spec: str = Form(...),
    source_language: str = Form("auto"),
    user: User = Depends(require_translation_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """返回实际 PDF 文件，客户端离开时取消仍在执行的版式任务。"""
    too_large = "PDF 文件不能超过 20 MiB"
    try:
        content = await read_upload_with_limit(file, max_size_bytes=MAX_PDF_UPLOAD_BYTES, too_large_message=too_large)
    except ValueError as exc:
        raise ContentTooLargeError(too_large, code="translation_pdf_too_large") from exc
    translation = asyncio.create_task(
        translate_pdf(
            db, uid=str(user.uid), model_spec=model_spec, source_language=source_language, target_language=target_language, content=content
        )
    )
    disconnected = asyncio.ensure_future(request.receive())
    try:
        await asyncio.wait((translation, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if not translation.done():
            raise HTTPException(status_code=499, detail="浏览器已断开连接")
        document = translation.result()
    finally:
        disconnected.cancel()
        with suppress(asyncio.CancelledError):
            await disconnected
        if not translation.done():
            translation.cancel()
            with suppress(asyncio.CancelledError):
                await translation
    filename = PurePosixPath((file.filename or "source.pdf").replace("\\", "/")).stem
    disposition = f"attachment; filename*=UTF-8''{quote(filename + '-translated.pdf', safe='')}"
    return Response(document, media_type="application/pdf", headers={"Cache-Control": "no-store", "Content-Disposition": disposition})
