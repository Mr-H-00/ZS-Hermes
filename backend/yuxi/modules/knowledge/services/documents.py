"""知识文档元数据、正文、窗口及原件读取用例，不装配检索执行器。"""

import json
import re
from typing import Any
from urllib.parse import unquote

from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.schemas import FindOutputSchema, FindWindowSchema
from yuxi.modules.knowledge.utils.kb_utils import sanitize_processing_params
from yuxi.shared.datetime import utc_isoformat
from yuxi.shared.errors import ContentTooLargeError, InvalidOperationError
from yuxi.shared.files import detect_media_type


async def get_file_basic_info(kb_id: str, file_id: str) -> dict:
    """获取文件基本信息（仅元数据）。"""
    return {"meta": await load_file_meta(kb_id, file_id)}


async def get_file_content(kb_id: str, file_id: str) -> dict:
    """获取文件内容信息（chunks 和 lines）。"""
    file_meta = await load_file_meta(kb_id, file_id)
    return await get_file_content_from_meta(file_id, file_meta)


async def get_file_info(kb_id: str, file_id: str) -> dict:
    """获取文件完整信息（基本信息+内容信息）。"""
    file_meta = await load_file_meta(kb_id, file_id)
    content_info = await get_file_content_from_meta(file_id, file_meta)
    return {"meta": file_meta, **content_info}


async def open_original_file(kb_id: str, file_id: str):
    """打开原件对象流，由 HTTP 适配层在流结束后关闭连接。"""
    meta = await load_file_meta(kb_id, file_id)
    if meta.get("is_folder"):
        raise InvalidOperationError("文件夹不支持下载")
    if meta.get("file_type") == "url":
        raise InvalidOperationError("URL 类型文件不支持下载原始文件")
    path = original_file_path(meta)
    if not path or not is_minio_url(path):
        raise InvalidOperationError("文件路径必须是 MinIO URL")
    filename = unquote(meta.get("filename") or "file")
    bucket, object_name = parse_minio_url(path)
    source = await get_minio_client().adownload_response(bucket_name=bucket, object_name=object_name)
    return source, filename, meta.get("content_type") or detect_media_type(filename)


async def get_file_download(kb_id: str, file_id: str, variant: str = "original") -> dict:
    """下载原始或解析后的文件内容。"""
    file_meta = await load_file_meta(kb_id, file_id)
    if file_meta.get("is_folder"):
        raise ValueError("Cannot download a folder")
    if variant not in {"original", "parsed"}:
        raise ValueError("Unsupported download variant")

    filename = file_meta.get("filename") or file_meta.get("original_filename") or file_id
    if variant == "parsed":
        markdown_file = file_meta.get("markdown_file")
        if not markdown_file:
            raise ValueError("文件尚未生成解析结果")
        return {
            "filename": f"{filename}.parsed.md",
            "content": await read_minio_bytes(markdown_file),
            "media_type": "text/markdown; charset=utf-8",
        }

    original_path = original_file_path(file_meta)
    if not original_path:
        raise ValueError("文件没有可下载的原始内容")
    media_type = file_meta.get("content_type") or detect_media_type(filename)
    return {
        "filename": filename,
        "content": await read_minio_bytes(original_path),
        "media_type": media_type,
    }


async def open_file_content(kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
    """按行窗口打开文件解析后的 Markdown 内容"""
    try:
        file_meta = await load_file_meta(kb_id, file_id)
    except ValueError as exc:
        raise ValueError(f"文件不存在: {file_id}") from exc
    if file_meta.get("is_folder"):
        raise ValueError(f"文件 {file_id} 是文件夹")

    markdown_file = file_meta.get("markdown_file")
    if not markdown_file:
        raise ValueError(f"文件 {file_id} 没有解析后的 Markdown 内容")

    content = await read_markdown_from_minio(markdown_file)
    return build_open_file_window(content, offset=offset, limit=limit)


async def find_file_content(
    kb_id: str,
    file_id: str,
    patterns: list[str],
    *,
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
) -> dict:
    """在解析后的 Markdown 中按关键词或正则查找上下文窗口。"""
    try:
        file_meta = await load_file_meta(kb_id, file_id)
    except ValueError as exc:
        raise ValueError(f"文件不存在: {file_id}") from exc
    if file_meta.get("is_folder"):
        raise ValueError(f"文件 {file_id} 是文件夹")

    markdown_file = file_meta.get("markdown_file")
    if not markdown_file:
        raise ValueError(f"文件 {file_id} 没有解析后的 Markdown 内容")

    content = await read_markdown_from_minio(markdown_file)
    return build_find_file_windows(
        content,
        patterns=patterns,
        use_regex=use_regex,
        case_sensitive=case_sensitive,
        max_windows=max_windows,
        window_size=window_size,
    )


def file_record_to_meta(record: Any) -> dict:
    """将持久化文件记录映射为通用元数据字典。"""
    return {
        "file_id": record.file_id,
        "kb_id": record.kb_id,
        "parent_id": record.parent_id,
        "filename": record.filename,
        "file_type": record.file_type,
        "path": record.path,
        "markdown_file": record.markdown_file,
        "status": record.status,
        "content_hash": record.content_hash,
        "size": record.file_size,
        "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
        "token_count": int(getattr(record, "token_count", 0) or 0),
        "content_type": record.content_type,
        "processing_params": sanitize_processing_params(record.processing_params),
        "is_folder": record.is_folder,
        "error": record.error_message,
        "created_by": record.created_by,
        "updated_by": record.updated_by,
        "created_at": utc_isoformat(record.created_at) if record.created_at else None,
        "updated_at": utc_isoformat(record.updated_at) if record.updated_at else None,
        "original_filename": record.original_filename,
        "minio_url": record.minio_url,
        "generation": int(getattr(record, "generation", 1) or 1),
        "active_generation": int(getattr(record, "active_generation", 1) or 1),
        "building_generation": getattr(record, "building_generation", None),
    }


def file_meta_to_record_data(meta: dict) -> dict[str, Any]:
    """将元数据字典还原为可持久化字段。"""
    return {
        "kb_id": meta.get("kb_id"),
        "parent_id": meta.get("parent_id"),
        "filename": meta.get("filename") or "",
        "original_filename": meta.get("original_filename"),
        "file_type": meta.get("file_type"),
        "path": meta.get("path"),
        "minio_url": meta.get("minio_url"),
        "markdown_file": meta.get("markdown_file"),
        "status": meta.get("status"),
        "content_hash": meta.get("content_hash"),
        "file_size": meta.get("size"),
        "chunk_count": int(meta.get("chunk_count") or 0),
        "token_count": int(meta.get("token_count") or 0),
        "content_type": meta.get("content_type"),
        "processing_params": sanitize_processing_params(meta.get("processing_params")),
        "is_folder": meta.get("is_folder", False),
        "error_message": meta.get("error"),
        "created_by": str(meta.get("created_by")) if meta.get("created_by") else None,
        "updated_by": str(meta.get("updated_by")) if meta.get("updated_by") else None,
    }


async def load_file_meta(kb_id: str, file_id: str) -> dict:
    """读取文件元数据并校验知识库归属，缺失时抛出 ValueError。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    record = await KnowledgeFileRepository().get_by_file_id(file_id)
    if record is None or record.kb_id != kb_id:
        raise ValueError(f"File {file_id} not found")

    return file_record_to_meta(record)


async def get_file_content_from_meta(file_id: str, file_meta: dict) -> dict:
    """读取完整解析文本，并校验片段行号与文件快照属于同一入库代次。"""
    from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository

    if (file_meta.get("processing_params") or {}).get("indexing_path") == "parent_child":
        from yuxi.modules.knowledge.repositories.parent_child_chunk_repository import KnowledgeParentChildChunkRepository

        parents = await KnowledgeParentChildChunkRepository().list_parents_by_file_id(file_id)
        result = {
            "lines": [
                {
                    "id": parent.parent_id,
                    "content": parent.parent_text,
                    "chunk_order_index": parent.parent_index,
                    "start_char_pos": parent.start_offset,
                    "end_char_pos": parent.end_offset,
                    "graph_indexed": parent.graph_indexed,
                    "ent_ids": parent.ent_ids,
                    "tags": parent.tags,
                    "extraction_result": parent.extraction_result,
                }
                for parent in parents
            ],
            "line_location": {"available": False, "reason": "parent_child_offsets"},
        }
        if file_meta.get("markdown_file"):
            result["content"] = await read_markdown_from_minio(file_meta["markdown_file"])
        return result
    content_info = {"lines": [], "line_location": {"available": False, "reason": "content_unavailable"}}
    location_reason = "document_not_indexed" if file_meta.get("status") not in {"indexed", "done"} else ""
    try:
        chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
        if not location_reason and any(chunk.generation != file_meta.get("active_generation") for chunk in chunks):
            location_reason = "index_changed"
        content_info["lines"] = [
            {
                "id": chunk.chunk_id,
                "content": chunk.content,
                "chunk_order_index": chunk.chunk_index,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "start_char_pos": chunk.start_char_pos,
                "end_char_pos": chunk.end_char_pos,
                "start_token_pos": chunk.start_token_pos,
                "end_token_pos": chunk.end_token_pos,
                "graph_indexed": chunk.graph_indexed,
                "ent_ids": chunk.ent_ids,
                "tags": chunk.tags,
                "extraction_result": chunk.extraction_result,
            }
            for chunk in chunks
        ]
    except Exception as e:
        location_reason = "chunks_unavailable"
        logger.error(f"Failed to get file content from PostgreSQL: {e}")

    if not content_info["lines"]:
        logger.warning(f"No chunks found in PostgreSQL for file {file_id}, file may not have been indexed")

    # Try to read markdown content if available
    if file_meta.get("markdown_file"):
        try:
            content = await read_markdown_from_minio(file_meta["markdown_file"])
            content_info["content"] = content
        except Exception as e:
            logger.error(f"Failed to read markdown file for {file_id}: {e}")

    if content_info.get("content"):
        content_info["line_location"] = {"available": not location_reason, "reason": location_reason or None}
    return content_info


def original_file_path(file_meta: dict) -> str | None:
    """返回原始文件位置：优先 MinIO 地址，回退历史 path。"""
    return file_meta.get("minio_url") or file_meta.get("path")


async def read_minio_bytes(file_path: str) -> bytes:
    """读取 MinIO 对象字节；路径格式非法时显式失败。"""
    if not file_path or not is_minio_url(file_path):
        raise ValueError(f"Invalid MinIO path format: {file_path}")

    bucket_name, object_name = parse_minio_url(file_path)
    minio_client = get_minio_client()
    return await minio_client.adownload_file(bucket_name, object_name)


async def read_markdown_from_minio(file_path: str) -> str:
    """读取 MinIO 中的 Markdown 文本。"""
    content_bytes = await read_minio_bytes(file_path)
    return content_bytes.decode("utf-8")


def build_open_file_window(content: str, *, offset: int = 0, limit: int = 800) -> dict[str, Any]:
    """按行窗口渲染带行号的内容。"""
    lines = content.splitlines()
    total_lines = len(lines)
    start = min(max(int(offset), 0), total_lines)
    window_size = min(max(int(limit), 1), 2000)
    # 预算包含行号、JSON 转义和窗口元数据，不能截断单行正文。
    byte_budget = 64 * 1024
    selected = [f"{start + index + 1:6d}\t{line}" for index, line in enumerate(lines[start : start + window_size])]

    def render(count: int) -> dict[str, Any]:
        """统一装配指定完整行前缀的响应。"""
        end = start + count
        return {
            "start_line": start + 1 if count else 0,
            "end_line": end,
            "total_lines": total_lines,
            "offset": start,
            "window_size": window_size,
            "has_more_before": start > 0,
            "has_more_after": end < total_lines,
            "next_offset": end if end < total_lines else None,
            "content": "\n".join(selected[:count]),
        }

    result = render(0)
    low, high = 1, len(selected)
    while low <= high:
        count = (low + high) // 2
        candidate = render(count)
        # MCP 同时携带文本和结构化结果；留出外层 JSON-RPC 标头空间。
        wire = {"content": [{"type": "text", "text": json.dumps(candidate, ensure_ascii=False)}], "structuredContent": candidate}
        if len(json.dumps(wire, ensure_ascii=True).encode("utf-8")) > byte_budget - 1024:
            high = count - 1
        else:
            result = candidate
            low = count + 1
    if selected and result["end_line"] == start:
        raise ContentTooLargeError("文档单行超过读取窗口字节上限", code="document_line_too_large")
    return result


def build_find_file_windows(
    content: str,
    *,
    patterns: list[str],
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
) -> dict[str, Any]:
    """按匹配行生成带上下文的查找窗口。"""
    patterns = [pattern for pattern in patterns if pattern]
    if not patterns:
        raise ValueError("请提供至少一个 pattern")

    lines = content.splitlines()
    flags = 0 if case_sensitive else re.IGNORECASE
    if use_regex:
        try:
            matchers = [re.compile(pattern, flags) for pattern in patterns]
        except re.error as exc:
            raise ValueError(f"无效正则表达式: {exc}") from exc

        def line_matches(line: str) -> bool:
            return any(matcher.search(line) for matcher in matchers)

    else:
        normalized_patterns = patterns if case_sensitive else [pattern.lower() for pattern in patterns]

        def line_matches(line: str) -> bool:
            haystack = line if case_sensitive else line.lower()
            return any(pattern in haystack for pattern in normalized_patterns)

    matched_indexes = [index for index, line in enumerate(lines) if line_matches(line)]
    windows: list[FindWindowSchema] = []
    covered_until = -1
    normalized_window_size = min(max(int(window_size), 1), 200)
    half_window = normalized_window_size // 2

    for matched_index in matched_indexes:
        if matched_index < covered_until:
            continue
        start = max(matched_index - half_window, 0)
        end = min(start + normalized_window_size, len(lines))
        # 窗口右缘触底时整体左移，保证窗口仍是完整 window_size 行。
        start = max(end - normalized_window_size, 0)
        matched_lines = [index + 1 for index in matched_indexes if start <= index < end]
        selected = lines[start:end]
        windows.append(
            FindWindowSchema(
                start_line=start + 1 if selected else 0,
                end_line=end,
                matched_lines=matched_lines,
                content="\n".join(f"{start + idx + 1:6d}\t{line}" for idx, line in enumerate(selected)),
            )
        )
        covered_until = end
        if len(windows) >= max_windows:
            break

    return FindOutputSchema(
        kb_id="",
        file_id="",
        semantic=False,
        match_mode="regex" if use_regex else "keyword",
        total_matches=len(matched_indexes),
        windows=windows,
    ).model_dump(exclude={"kb_id", "file_id"})
