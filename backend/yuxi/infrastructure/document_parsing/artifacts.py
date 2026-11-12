"""解析产物的相对资源路径与 Markdown 引用。"""

import posixpath
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

from yuxi.infrastructure.document_parsing.text_encoding import decode_user_text


def save_resource(output_dir: Path, relative_path: str, content: bytes) -> str:
    """在解析目录内保存资源，拒绝上级路径与符号链接。"""
    relative = resource_path(relative_path)
    target = output_dir / relative
    ensure_no_symlink_ancestors(target, output_dir, error_message=f"资源路径包含符号链接: {relative_path}")
    # 输出目录由 parser 独占创建，不允许引擎覆盖已有资源。
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(content)
    return relative


def resource_path(value: str) -> str:
    """校验解析产物中的相对 POSIX 资源路径。"""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or ":" in value or ".." in path.parts:
        raise ValueError(f"不安全的资源路径: {value}")
    return path.as_posix()


def ensure_no_symlink_ancestors(path: Path, boundary: Path, *, error_message: str) -> None:
    """拒绝路径自身到边界之间任一符号链接。"""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(error_message)
        if part == boundary:
            break


def local_image_links(markdown: str) -> list[str]:
    """读取 Markdown 图片引用，保留完整相对路径身份。"""
    links = []
    for token in MarkdownIt().parse(markdown):
        for child in token.children or []:
            if child.type == "image":
                src = child.attrGet("src") or ""
                if src and not urlsplit(src).scheme and not src.startswith("//"):
                    links.append(src)
    return list(dict.fromkeys(links))


def replace_image_links(markdown: str, mapping: dict[str, str]) -> str:
    """按完整链接替换内联图片及引用定义，保留标题。"""
    # 目标先规范化为可直接写入 Markdown 的地址，再补齐解码与括号转义变体。
    replacements = {
        source: target if target.startswith("/") or urlsplit(target).scheme else quote(target, safe="/")
        for source, target in mapping.items()
    }
    for source, target in list(replacements.items()):
        for variant in (source, unquote(source)):
            replacements.setdefault(variant, target)
            replacements.setdefault(re.sub(r"([()])", r"\\\1", variant), target)
    if not replacements:
        return markdown
    choices = "|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True))
    inline = r"(\]\(<?)(" + choices + r")(?=>?(?:\s|\)))"
    definitions = r"(?m)^( {0,3}\[[^\]]+\]:\s*<?)(" + choices + r")(?=>?(?:\s|$))"
    for pattern in (inline, definitions):
        markdown = re.sub(pattern, lambda match: match[1] + replacements[match[2]], markdown)
    return markdown


def extract_markdown_archive(zip_path: str, output_dir: Path) -> str:
    """提取 Markdown 引用的资源，拒绝归档越界、重名和符号链接。"""
    with zipfile.ZipFile(zip_path) as archive:
        names: set[str] = set()
        # 第一遍只校验归档路径与条目类型，不写入任何内容。
        for entry in archive.infolist():
            name = entry.filename
            resource_path(name)
            if stat.S_ISLNK(entry.external_attr >> 16) or name in names:
                raise ValueError(f"ZIP 包含符号链接或重复路径: {name}")
            names.add(name)

        md_files = sorted(name for name in names if name.lower().endswith(".md"))
        if not md_files:
            raise ValueError("压缩包中未找到 .md 文件")
        md_file = next((name for name in md_files if PurePosixPath(name).name == "full.md"), md_files[0])
        markdown = decode_user_text(archive.read(md_file), md_file)

        # 第二遍复制正文引用的图片，并改写为输出目录内的相对路径。
        mapping: dict[str, str] = {}
        copied: set[str] = set()
        for link in local_image_links(markdown):
            source = posixpath.normpath(posixpath.join(posixpath.dirname(md_file), unquote(link)))
            resource_path(source)
            if source not in names:
                raise ValueError(f"ZIP 缺少 Markdown 资源: {link}")
            target = f"images/{source}"
            if target not in copied:
                save_resource(output_dir, target, archive.read(source))
                copied.add(target)
            mapping[link] = target
        return replace_image_links(markdown, mapping)
