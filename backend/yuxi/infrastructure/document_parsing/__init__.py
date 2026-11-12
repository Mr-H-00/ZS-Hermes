"""文档解析的输入、产物、格式与错误契约。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """已解析的引擎配置与本次转换参数。"""

    ocr_engine: str = "disable"
    processor_kwargs: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseResult:
    """调用方拥有的输出目录、Markdown 入口与资源相对路径。"""

    directory: Path
    markdown_path: Path
    resources: tuple[str, ...]


TEXT_FILE_EXTENSIONS = (".txt", ".md")
OFFICE_FILE_EXTENSIONS = (".docx", ".pptx", ".xls", ".xlsx")
HTML_FILE_EXTENSIONS = (".html", ".htm")
IMAGE_FILE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp")
PDF_FILE_EXTENSIONS = (".pdf",)
OCR_FILE_EXTENSIONS = frozenset((*PDF_FILE_EXTENSIONS, *IMAGE_FILE_EXTENSIONS))

SUPPORTED_FILE_EXTENSIONS = (
    *TEXT_FILE_EXTENSIONS,
    *OFFICE_FILE_EXTENSIONS,
    *HTML_FILE_EXTENSIONS,
    ".json",
    ".csv",
    *PDF_FILE_EXTENSIONS,
    *IMAGE_FILE_EXTENSIONS,
    ".zip",
)


class DocumentProcessorException(Exception):
    """文档处理异常基类。"""

    def __init__(self, message: str, service_name: str | None = None, status_code: str | None = None):
        super().__init__(message)
        self.message = message
        self.service_name = service_name
        self.status_code = status_code

    def __str__(self) -> str:
        if self.service_name:
            return f"[{self.service_name}] {self.message}"
        return self.message


class OCRException(DocumentProcessorException):
    """OCR 处理异常。"""


class DocumentParserException(DocumentProcessorException):
    """文档解析异常。"""


def is_supported_file_extension(file_name: str | Path) -> bool:
    """判断文件名是否属于统一解析器支持的输入格式。"""
    return Path(file_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS
