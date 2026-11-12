"""Office 转换工具的产物与失败边界。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.infrastructure import office_conversion as conversion


@pytest.mark.parametrize(
    ("filename", "supported"),
    [("demo.docx", True), ("demo.PPTX", True), ("demo.xlsx", False), ("demo.doc", False), ("demo.ppt", False)],
)
def test_office_pdf_conversion_only_supports_docx_and_pptx(filename, supported):
    assert conversion.is_office_pdf_convertible(filename) is supported


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".docx", ".pptx"])
async def test_office_conversion_returns_pdf_and_cleans_temporary_files(monkeypatch, suffix):
    monkeypatch.setattr(conversion.shutil, "which", lambda _name: "/usr/bin/soffice")
    temp_paths = []

    def run(command, *, capture_output, timeout, check):
        input_path = Path(command[-1])
        output_dir = Path(command[command.index("--outdir") + 1])
        assert input_path == output_dir / f"source{suffix}"
        assert input_path.read_bytes() == b"office content"
        assert command[1:6] == ["--headless", "--nologo", "--nofirststartwizard", "--nodefault", "--nolockcheck"]
        assert command[6] == f"-env:UserInstallation={(output_dir / 'lo-profile').as_uri()}"
        assert command[7:9] == ["--convert-to", "pdf"]
        assert capture_output is True
        assert timeout == conversion.OFFICE_CONVERSION_TIMEOUT_SECONDS
        assert check is False
        temp_paths.append(output_dir)
        (output_dir / "source.pdf").write_bytes(b"%PDF-1.4\nconverted")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(conversion.subprocess, "run", run)

    result = await conversion.convert_office_to_pdf(f"../../report{suffix}", b"office content")

    assert result == b"%PDF-1.4\nconverted"
    assert temp_paths and all(not path.exists() for path in temp_paths)


@pytest.mark.asyncio
async def test_office_conversion_rejects_unsupported_format_before_process(monkeypatch):
    def fail_lookup(_name):
        raise AssertionError("unsupported format must not start conversion")

    monkeypatch.setattr(conversion.shutil, "which", fail_lookup)

    with pytest.raises(conversion.OfficeConversionError, match="当前文件类型不支持"):
        await conversion.convert_office_to_pdf("table.xlsx", b"office content")


@pytest.mark.asyncio
async def test_office_conversion_reports_missing_executable(monkeypatch):
    monkeypatch.setattr(conversion.shutil, "which", lambda _name: None)

    with pytest.raises(conversion.OfficeConversionError, match="依赖 LibreOffice"):
        await conversion.convert_office_to_pdf("report.docx", b"office content")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("timeout", "转换 PDF 超时"),
        ("exit", "转换 PDF 失败: converter failed"),
        ("missing", "未生成 PDF 文件"),
        ("invalid", "输出文件不是有效 PDF"),
    ],
)
async def test_office_conversion_reports_failures_and_cleans_temporary_files(monkeypatch, failure, message):
    monkeypatch.setattr(conversion.shutil, "which", lambda _name: "/usr/bin/soffice")
    temp_paths = []

    def run(command, **kwargs):
        output_dir = Path(command[command.index("--outdir") + 1])
        temp_paths.append(output_dir)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "invalid":
            (output_dir / "source.pdf").write_bytes(b"not a pdf")
        return SimpleNamespace(
            returncode=1 if failure == "exit" else 0,
            stdout=b"",
            stderr=b"converter failed" if failure == "exit" else b"",
        )

    monkeypatch.setattr(conversion.subprocess, "run", run)

    with pytest.raises(conversion.OfficeConversionError, match=message):
        await conversion.convert_office_to_pdf("report.docx", b"office content")

    assert temp_paths and all(not path.exists() for path in temp_paths)
