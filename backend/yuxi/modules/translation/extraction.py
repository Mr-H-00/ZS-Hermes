"""将翻译页面上传的单份文档提取为用户可编辑的正文。"""

import asyncio
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.document_parsing import (
    IMAGE_FILE_EXTENSIONS,
    OFFICE_FILE_EXTENSIONS,
    PDF_FILE_EXTENSIONS,
    TEXT_FILE_EXTENSIONS,
    DocumentProcessorException,
    ParseOptions,
)
from yuxi.infrastructure.document_parsing.text_encoding import decode_user_text
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.documents.service import resolve_parse_options
from yuxi.modules.translation.services import MAX_SOURCE_CHARACTERS
from yuxi.shared.errors import ContentTooLargeError, DomainError, InvalidInputError, ServiceUnavailableError, UpstreamError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_SUPPORTED_EXTENSIONS = frozenset((*TEXT_FILE_EXTENSIONS, *OFFICE_FILE_EXTENSIONS, *PDF_FILE_EXTENSIONS, *IMAGE_FILE_EXTENSIONS, ".csv"))


async def extract_translation_text(db: AsyncSession, *, filename: str, content: bytes) -> str:
    """在请求专属目录内解析受支持的文件，不发布文件资源。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        raise InvalidInputError("请选择图片、PDF、Word、PPT、Excel 或文本文件", code="translation_file_unsupported")
    if not content:
        raise InvalidInputError("上传的文件为空", code="translation_file_required")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ContentTooLargeError("上传文件不能超过 10 MiB", code="translation_file_too_large")
    if suffix in TEXT_FILE_EXTENSIONS:
        text = await asyncio.to_thread(decode_user_text, content, f"source{suffix}")
        return _require_usable_text(text)

    # 文档解析器与 OCR 依赖按请求加载，避免影响 API 的其他启动链路。
    from yuxi.infrastructure.document_parsing.parser import parse

    with tempfile.TemporaryDirectory(prefix="yuxi-translate-") as directory:
        source = Path(directory) / f"source{suffix}"
        await await_io(asyncio.to_thread(source.write_bytes, content))
        try:
            if suffix in IMAGE_FILE_EXTENSIONS:
                options = await _ocr_options(db)
            else:
                options = ParseOptions()
                await db.rollback()
            parsed = await parse(source, Path(directory) / "extracted", options)
            text = await _read_text(parsed.markdown_path)
            if suffix in PDF_FILE_EXTENSIONS and not text.strip():
                # 可搜索 PDF 优先使用本地文本层。仅扫描件需要管理员配置的 OCR。
                options = await _ocr_options(db)
                parsed = await parse(source, Path(directory) / "recognized", options)
                text = await _read_text(parsed.markdown_path)
        except DomainError:
            raise
        except DocumentProcessorException as exc:
            if exc.service_name == "pdf_preflight":
                raise InvalidInputError("PDF 无法读取或需要密码", code="translation_file_invalid") from exc
            raise UpstreamError("文件识别失败，请检查 OCR 配置后重试", code="translation_file_ocr_failed") from exc
        except (ValueError, OSError) as exc:
            raise InvalidInputError("无法读取文件内容，请检查文件格式", code="translation_file_invalid") from exc
        except Exception as exc:
            logger.warning("翻译文件提取失败，格式={}，错误类型={}", suffix, type(exc).__name__)
            raise UpstreamError("未能提取文件内容，请检查文件后重试", code="translation_file_parse_failed") from exc
    return _require_usable_text(text)


async def _ocr_options(db: AsyncSession) -> ParseOptions:
    """读取站点 OCR 配置，在实际提取之前归还数据库连接。"""
    try:
        options = await resolve_parse_options(db=db)
    except (ValueError, DocumentProcessorException) as exc:
        raise ServiceUnavailableError("文件需要配置可用的 OCR 服务", code="translation_ocr_unavailable") from exc
    finally:
        await db.rollback()
    if options.ocr_engine == "disable":
        raise ServiceUnavailableError("文件需要 OCR，请在 Yuxi 中配置可用的 OCR 服务", code="translation_ocr_unavailable")
    return options


async def _read_text(path: Path) -> str:
    """限制返回浏览器的文本大小，避免悄悄发送部分文档。"""

    def read() -> str:
        """读取足以判断正文是否超限的字符。"""
        with path.open(encoding="utf-8") as stream:
            return stream.read(MAX_SOURCE_CHARACTERS + 1)

    return await await_io(asyncio.to_thread(read))


def _require_usable_text(text: str) -> str:
    """校验正文预算与可翻译性，保留已验证文本的原排版。"""
    if len(text) > MAX_SOURCE_CHARACTERS:
        raise ContentTooLargeError(f"文件正文超过 {MAX_SOURCE_CHARACTERS} 个字符，请拆分后上传", code="translation_text_too_long")
    if not text.strip():
        raise InvalidInputError("未从文件中提取到可翻译的文本", code="translation_file_no_text")
    return text.replace("\x00", "\ufffd")
