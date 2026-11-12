"""用户上传文本的编码识别与解码测试。"""

from __future__ import annotations

import codecs
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from yuxi.infrastructure.document_parsing.text_encoding import decode_user_text
from yuxi.modules.documents.service import parse


async def _parse_markdown(source, params=None, db=None):
    with tempfile.TemporaryDirectory() as temporary:
        result = await parse(str(source), Path(temporary) / "parsed", params, db)
        return result.markdown_path.read_text(encoding="utf-8")


def test_decode_user_text_detects_legacy_codecs() -> None:
    samples = {
        "gbk.txt": "设备清单：温度传感器、雷达",
        "big5.txt": "設備清單：溫度感測器、雷達",
        "gb18030.txt": "生僻字符：𠀀𠀁",
    }
    for name, text in samples.items():
        raw = text.encode("gb18030" if name == "gb18030.txt" else ("big5" if name == "big5.txt" else "gbk"))
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
        assert decode_user_text(raw, name) == text


def test_decode_user_text_strips_utf8_bom() -> None:
    raw = codecs.BOM_UTF8 + "带 BOM 的文本".encode()
    assert raw.decode("utf-8").startswith("\ufeff")
    assert decode_user_text(raw, "bom.txt") == "带 BOM 的文本"


def test_decode_user_text_routes_nul_bytes_to_detector() -> None:
    raw = "装备清单 alpha 段".encode("utf-16-le")
    decoded = decode_user_text(raw, "utf16le-no-bom.txt")
    assert "\x00" not in decoded
    assert decoded == "装备清单 alpha 段"


def test_decode_user_text_replaces_truncated_utf16_tail() -> None:
    raw = codecs.BOM_UTF16_LE + b"A\x00B\x00C"
    decoded = decode_user_text(raw, "truncated.txt")
    assert decoded.startswith("AB")
    assert "\ufffd" in decoded


def test_decode_user_text_accepts_empty_upload() -> None:
    assert decode_user_text(b"", "empty.txt") == ""


async def test_parse_accepts_non_utf8_text_uploads(tmp_path: Path) -> None:
    gbk = "中文内容，编码为 GBK。"
    txt = tmp_path / "legacy.txt"
    txt.write_bytes(gbk.encode("gbk"))
    assert gbk in await _parse_markdown(txt)

    md = tmp_path / "legacy.md"
    md.write_bytes(f"# 标题\n\n{gbk}".encode("gbk"))
    assert gbk in await _parse_markdown(md)

    html = tmp_path / "legacy.html"
    html.write_bytes(f"<html><body><p>{gbk}</p></body></html>".encode("gbk"))
    assert gbk in await _parse_markdown(html, params={"html_text_only": True})

    payload = tmp_path / "legacy.json"
    payload.write_bytes(json.dumps({"note": gbk}, ensure_ascii=False).encode("gbk"))
    assert gbk in await _parse_markdown(payload)

    csv_file = tmp_path / "legacy.csv"
    csv_file.write_bytes(f"名称,说明\n设备,{gbk}\n".encode("gbk"))
    assert gbk in await _parse_markdown(csv_file)


async def test_parse_accepts_non_utf8_markdown_inside_zip(tmp_path: Path) -> None:
    gbk = "压缩包内的 GBK 文档。"
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/legacy.md", f"# 标题\n\n{gbk}".encode("gbk"))
    markdown = await _parse_markdown(archive_path)
    assert gbk in markdown
