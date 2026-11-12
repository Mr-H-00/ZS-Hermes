"""使用 LibreOffice 将受支持的 Office 文件转换为 PDF。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

_OFFICE_PDF_EXTENSIONS = frozenset({".docx", ".pptx"})


def _office_conversion_timeout_seconds() -> int:
    """读取既有部署配置中的转换超时。"""
    # 预览是可选能力；既有部署可能保留非数字值，非法配置回退默认超时。
    raw = os.getenv("OFFICE_PREVIEW_TIMEOUT_SECONDS", "60")
    try:
        return int(raw)
    except ValueError:
        return 60


OFFICE_CONVERSION_TIMEOUT_SECONDS = _office_conversion_timeout_seconds()


class OfficeConversionError(RuntimeError):
    """Office 文件转换为 PDF 失败。"""


async def convert_office_to_pdf(filename: str, content: bytes) -> bytes:
    """把受支持的 Office 文件字节转换为 PDF。"""
    return await asyncio.to_thread(_convert_office_to_pdf_sync, filename, content)


def is_office_pdf_convertible(path: str) -> bool:
    """判断是否属于支持转换为 PDF 的 Office 格式。"""
    return PurePosixPath(path).suffix.lower() in _OFFICE_PDF_EXTENSIONS


def _convert_office_to_pdf_sync(filename: str, content: bytes) -> bytes:
    """在独立临时目录和 LibreOffice profile 中执行转换。"""
    suffix = PurePosixPath(filename).suffix.lower()
    if not is_office_pdf_convertible(filename):
        raise OfficeConversionError("当前文件类型不支持转换为 PDF 预览")

    executable = _office_converter_executable()
    with tempfile.TemporaryDirectory(prefix="yuxi-office-preview-") as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / f"source{suffix}"
        output_path = temp_path / "source.pdf"
        profile_path = temp_path / "lo-profile"
        profile_path.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(content)

        command = [
            executable,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--nodefault",
            "--nolockcheck",
            f"-env:UserInstallation={profile_path.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(temp_path),
            str(input_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=OFFICE_CONVERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfficeConversionError(f"Office 文件转换 PDF 超时（{OFFICE_CONVERSION_TIMEOUT_SECONDS} 秒）") from exc
        detail = (result.stderr or result.stdout).decode("utf-8", errors="ignore").strip()
        if result.returncode != 0:
            raise OfficeConversionError(f"Office 文件转换 PDF 失败: {detail or 'LibreOffice 执行失败'}")
        if not output_path.exists():
            raise OfficeConversionError(f"Office 文件转换 PDF 失败: 未生成 PDF 文件。{detail}")

        pdf_content = output_path.read_bytes()
        if not pdf_content.startswith(b"%PDF-"):
            raise OfficeConversionError("Office 文件转换 PDF 失败: 输出文件不是有效 PDF")
        return pdf_content


def _office_converter_executable() -> str:
    """定位可用的 LibreOffice 执行器。"""
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise OfficeConversionError("Office PDF 预览依赖 LibreOffice，请先安装 soffice/libreoffice")
    return executable
