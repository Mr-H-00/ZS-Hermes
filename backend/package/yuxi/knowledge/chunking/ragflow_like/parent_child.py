"""Parent-Child 切块的纯文本实现。

该模块只负责依据解析边界生成可回溯的父块、子块和 span，不负责写入数据库或向量库。
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast


class TokenizerUnavailableError(ValueError):
    """Parent-Child 切块无法获得可用 tokenizer 时抛出的错误。"""


class Tokenizer(Protocol):
    """定义切块所需的最小 tokenizer 接口。"""

    def encode(self, text: str) -> Sequence[Any]:
        """把文本编码为 token 序列。"""


type TokenizerLike = Tokenizer | Callable[[str], Sequence[Any]]
type SegmentInput = str | Mapping[str, Any] | Sequence[Any]
type PageInput = Mapping[str, Any] | Sequence[Any]

DEFAULT_PARENT_TOKEN_NUM = 1000
DEFAULT_CHILD_TOKEN_NUM = 200
DEFAULT_CHILD_OVERLAP_PERCENT = 15
DEFAULT_SEPARATOR = "\\n"


def resolve_tokenizer(
    provider_tokenizer: TokenizerLike | None = None,
    *,
    tokenizer_id: str | None = None,
) -> TokenizerLike:
    """按 provider tokenizer、再按 tiktoken 的顺序解析 tokenizer。

    Args:
        provider_tokenizer: 嵌入 provider 提供的 tokenizer 或 ``encode`` 可调用对象。
        tokenizer_id: provider tokenizer 的诊断标识，仅用于错误信息。

    Returns:
        可接受文本并返回 token 序列的 tokenizer。

    Raises:
        TokenizerUnavailableError: 两级 tokenizer 都不可用或接口不合法。
    """
    if provider_tokenizer is not None:
        if _is_tokenizer_like(provider_tokenizer):
            return provider_tokenizer
        identifier = f" ({tokenizer_id})" if tokenizer_id else ""
        raise TokenizerUnavailableError(f"provider tokenizer{identifier} 不提供可用的 encode 接口")

    try:
        module = importlib.import_module("tiktoken")
        encoding = module.get_encoding("cl100k_base")
    except Exception as exc:
        raise TokenizerUnavailableError(f"provider tokenizer 不可用，且 tiktoken 不可用: {exc}") from exc

    if not _is_tokenizer_like(encoding):
        raise TokenizerUnavailableError("tiktoken tokenizer 不提供可用的 encode 接口")
    return encoding


def chunk_parent_child(
    markdown_content: str,
    file_id: str,
    kb_id: str,
    version_id: str,
    processing_params: Mapping[str, Any] | None = None,
    *,
    parsed_segments: Sequence[SegmentInput] | None = None,
    page_spans: Sequence[PageInput] | None = None,
    tokenizer: TokenizerLike | None = None,
    provider_tokenizer: TokenizerLike | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """根据解析边界生成 Parent-Child 父块、子块和原文 span。

    Args:
        markdown_content: 解析后的 Markdown 原文，offset 以 Python 字符为单位。
        file_id: 文件标识，参与父块和子块的确定性 ID。
        kb_id: 知识库标识，参与确定性 ID。
        version_id: 文档版本标识，参与确定性 ID。
        processing_params: 含 ``parent_child`` 配置的最终处理参数。
        parsed_segments: 解析器输出的候选边界。映射值必须含 offset；字符串按原文顺序定位。
        page_spans: 页码到原文区间的映射，元素可为映射或 ``(start, end, page_num)``。
        tokenizer: 已解析 tokenizer；传入时优先于 provider tokenizer。
        provider_tokenizer: 嵌入 provider 提供的 tokenizer。

    Returns:
        包含 ``parents`` 和 ``children`` 两个列表的结果；父子正文均直接切自原文。

    Raises:
        ValueError: 参数、解析边界或页码区间非法。
        TokenizerUnavailableError: 无法获得 tokenizer。
    """
    text = _require_text(markdown_content)
    config = _normalize_config(processing_params)
    active_tokenizer = resolve_tokenizer(
        tokenizer if tokenizer is not None else provider_tokenizer,
        tokenizer_id=config.get("tokenizer_id"),
    )

    candidates = _resolve_segments(text, parsed_segments)
    atoms = _split_oversized_intervals(text, candidates, config["parent_token_num"], active_tokenizer)
    parent_intervals = _merge_parent_intervals(text, atoms, config["parent_token_num"], active_tokenizer)

    normalized_pages = _normalize_page_spans(page_spans, len(text))
    parents: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    for parent_index, (start, end) in enumerate(parent_intervals):
        parent_id = _stable_id("parent", kb_id, file_id, version_id, parent_index, start, end)
        parent_text = text[start:end]
        parent = {
            "parent_id": parent_id,
            "parent_index": parent_index,
            "parent_text": parent_text,
            "start_offset": start,
            "end_offset": end,
            "token_count": _token_count(active_tokenizer, parent_text),
            "metadata": {},
        }
        parents.append(parent)

        child_intervals = _split_child_interval(
            text,
            start,
            end,
            config["child_token_num"],
            config["child_overlap_percent"],
            active_tokenizer,
            config["separator"],
        )
        for child_index, (child_start, child_end) in enumerate(child_intervals):
            child_id = _stable_id("child", kb_id, file_id, version_id, parent_id, child_index, child_start, child_end)
            child_text = text[child_start:child_end]
            children.append(
                {
                    "child_id": child_id,
                    "parent_id": parent_id,
                    "child_index": child_index,
                    "child_text": child_text,
                    "start_offset": child_start,
                    "end_offset": child_end,
                    "token_count": _token_count(active_tokenizer, child_text),
                    "spans": _build_spans(child_start, child_end, normalized_pages),
                    "metadata": {},
                }
            )

    return {"parents": parents, "children": children}


def chunk_markdown_parent_child(
    markdown_content: str,
    file_id: str,
    kb_id: str,
    version_id: str,
    processing_params: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    """复用现有 Markdown 策略生成候选边界后执行 Parent-Child 切块。"""
    if "parsed_segments" not in kwargs:
        from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown

        filename = str(kwargs.pop("filename", "") or "")
        records = chunk_markdown(markdown_content, file_id, filename, dict(processing_params or {}))
        kwargs["parsed_segments"] = [
            {
                "start_offset": record["start_char_pos"],
                "end_offset": record["end_char_pos"],
            }
            if record.get("start_char_pos") is not None and record.get("end_char_pos") is not None
            else record["content"]
            for record in records
        ]
    return chunk_parent_child(markdown_content, file_id, kb_id, version_id, processing_params, **kwargs)


def _is_tokenizer_like(value: object) -> bool:
    """判断对象是否提供切块所需的 encode 接口。"""
    return callable(value) or callable(getattr(value, "encode", None))


def _token_count(tokenizer: TokenizerLike, text: str) -> int:
    """调用 tokenizer 并将 token 数转换为严格非负整数。"""
    try:
        if callable(tokenizer) and not callable(getattr(tokenizer, "encode", None)):
            tokens = tokenizer(text)
        else:
            encoder = cast(Tokenizer, tokenizer)
            tokens = encoder.encode(text)
        count = len(tokens)
    except Exception as exc:
        raise TokenizerUnavailableError(f"tokenizer 编码失败: {exc}") from exc
    if count < 0:
        raise TokenizerUnavailableError("tokenizer 返回了非法 token 数")
    return count


def _require_text(value: object) -> str:
    """校验输入文档为字符串，并拒绝空文档。"""
    if not isinstance(value, str):
        raise ValueError("markdown_content 必须是字符串")
    if not value.strip():
        return ""
    return value


def _normalize_config(processing_params: Mapping[str, Any] | None) -> dict[str, Any]:
    """校验 Parent-Child 参数并补齐稳定默认值。"""
    if processing_params is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(processing_params, Mapping):
        raw = processing_params
    else:
        raise ValueError("processing_params 必须是对象")

    nested = raw.get("parent_child", raw)
    if not isinstance(nested, Mapping):
        raise ValueError("parent_child 必须是对象")

    enabled = nested.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("parent_child.enabled 必须是布尔值")
    if not enabled:
        raise ValueError("Parent-Child 切块要求 parent_child.enabled=true")

    parent_tokens = _strict_int(nested.get("parent_token_num", DEFAULT_PARENT_TOKEN_NUM), "parent_token_num", 256, 4096)
    child_tokens = _strict_int(nested.get("child_token_num", DEFAULT_CHILD_TOKEN_NUM), "child_token_num", 64, 1024)
    overlap = nested.get("child_overlap_percent", DEFAULT_CHILD_OVERLAP_PERCENT)
    if isinstance(overlap, bool) or not isinstance(overlap, (int, float)):
        raise ValueError("child_overlap_percent 必须是 0..99 的整数")
    if isinstance(overlap, float) and (not overlap.is_integer() or not overlap == overlap):
        raise ValueError("child_overlap_percent 必须是 0..99 的整数")
    overlap = int(overlap)
    if not 0 <= overlap <= 99:
        raise ValueError("child_overlap_percent 必须是 0..99 的整数")
    if parent_tokens <= child_tokens:
        raise ValueError("parent_token_num 必须大于 child_token_num")

    separator = nested.get("separator", DEFAULT_SEPARATOR)
    if not isinstance(separator, str) or not separator:
        raise ValueError("parent_child.separator 必须是非空字符串")
    tokenizer_id = nested.get("tokenizer_id")
    if tokenizer_id is not None and (not isinstance(tokenizer_id, str) or not tokenizer_id):
        raise ValueError("parent_child.tokenizer_id 必须是非空字符串")
    return {
        "parent_token_num": parent_tokens,
        "child_token_num": child_tokens,
        "child_overlap_percent": overlap,
        "separator": separator,
        "tokenizer_id": tokenizer_id,
    }


def _strict_int(value: object, name: str, minimum: int, maximum: int) -> int:
    """校验整数参数及其闭区间边界。"""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum}..{maximum} 的整数")
    return value


def _resolve_segments(text: str, segments: Sequence[SegmentInput] | None) -> list[tuple[int, int]]:
    """把解析器候选边界解析为原文中的非重叠字符区间。"""
    if not text:
        return []
    if segments is None:
        return [(0, len(text))]

    resolved: list[tuple[int, int]] = []
    search_from = 0
    for item in segments:
        if isinstance(item, str):
            value = item
            found = text.find(value, search_from)
            if found < 0:
                stripped = value.strip()
                found = text.find(stripped, search_from) if stripped else -1
                value = stripped
            if found < 0 or not value:
                raise ValueError("解析器候选片段无法在原文中定位")
            start, end = found, found + len(value)
        elif isinstance(item, Mapping):
            start, end = _read_interval(item, len(text), "解析器候选片段")
            candidate_text = item.get("text", item.get("content"))
            if candidate_text is not None and candidate_text != text[start:end]:
                raise ValueError("解析器候选片段文本与 offset 指向的原文不一致")
        elif isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)) and len(item) >= 2:
            start, end = _read_interval({"start_offset": item[0], "end_offset": item[1]}, len(text), "解析器候选片段")
        else:
            raise ValueError("解析器候选片段必须是字符串、对象或 offset 序列")
        if resolved and start < resolved[-1][1]:
            raise ValueError("解析器候选片段存在重叠或未排序 offset")
        resolved.append((start, end))
        search_from = end
    return resolved or [(0, len(text))]


def _read_interval(value: Mapping[str, Any], text_length: int, label: str) -> tuple[int, int]:
    """读取并验证半开字符区间。"""
    start, end = value.get("start_offset"), value.get("end_offset")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= text_length:
        raise ValueError(f"{label} offset 非法")
    return start, end


def _split_oversized_intervals(
    text: str,
    intervals: Sequence[tuple[int, int]],
    max_tokens: int,
    tokenizer: TokenizerLike,
) -> list[tuple[int, int]]:
    """把超过父块上限的候选区间按原文硬切。"""
    atoms: list[tuple[int, int]] = []
    for start, end in intervals:
        if _token_count(tokenizer, text[start:end]) <= max_tokens:
            atoms.append((start, end))
            continue
        atoms.extend(_split_interval_by_tokens(text, start, end, max_tokens, tokenizer))
    return atoms


def _merge_parent_intervals(
    text: str,
    atoms: Sequence[tuple[int, int]],
    max_tokens: int,
    tokenizer: TokenizerLike,
) -> list[tuple[int, int]]:
    """按父块 token 上限合并候选区间并保留候选之间的原文空白。"""
    parents: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in atoms:
        if current is None:
            current = (start, end)
            continue
        candidate = (current[0], end)
        if _token_count(tokenizer, text[candidate[0] : candidate[1]]) <= max_tokens:
            current = candidate
            continue
        parents.append(current)
        current = (start, end)
    if current is not None:
        parents.append(current)
    return parents


def _split_interval_by_tokens(
    text: str,
    start: int,
    end: int,
    max_tokens: int,
    tokenizer: TokenizerLike,
) -> list[tuple[int, int]]:
    """在给定原文区间内按 token 上限切分，并返回字符区间。"""
    total = _token_count(tokenizer, text[start:end])
    if total <= max_tokens:
        return [(start, end)] if text[start:end].strip() else []

    result: list[tuple[int, int]] = []
    token_start = 0
    previous_end = 0
    while token_start < total:
        token_end = min(token_start + max_tokens, total)
        local_start = max(previous_end, _offset_for_token_count(text[start:end], token_start, tokenizer))
        local_end = _fit_end_for_token_limit(
            text[start:end],
            local_start,
            _offset_for_token_count(text[start:end], token_end, tokenizer),
            max_tokens,
            tokenizer,
        )
        if local_end <= local_start:
            raise TokenizerUnavailableError("tokenizer 无法提供可单调切分的 token 边界")
        result.append((start + local_start, start + local_end))
        previous_end = local_end
        token_start = token_end
    return result


def _split_child_interval(
    text: str,
    start: int,
    end: int,
    child_tokens: int,
    overlap_percent: int,
    tokenizer: TokenizerLike,
    separator: str,
) -> list[tuple[int, int]]:
    """在单个父块内按 token 窗口切子块并计算重叠步长。"""
    parent_text = text[start:end]
    separator = _unescape_separator(separator)
    total = _token_count(tokenizer, parent_text)
    if total == 0:
        return []
    overlap_tokens = child_tokens * overlap_percent // 100
    step_tokens = child_tokens - overlap_tokens
    if step_tokens <= 0:
        raise ValueError("child_overlap_percent 产生了非法步长")

    intervals: list[tuple[int, int]] = []
    token_start = 0
    previous_end = 0
    while token_start < total:
        token_end = min(token_start + child_tokens, total)
        local_start = _offset_for_token_count(parent_text, token_start, tokenizer)
        if intervals and local_start > previous_end:
            local_start = previous_end
        target_end = _offset_for_token_count(parent_text, token_end, tokenizer)
        if token_end >= total:
            target_end = len(parent_text)
        else:
            preferred_end = _separator_end_before(parent_text, local_start, target_end, separator)
            if preferred_end is not None:
                target_end = preferred_end
        local_end = _fit_end_for_token_limit(parent_text, local_start, target_end, child_tokens, tokenizer)
        if local_end <= local_start:
            raise TokenizerUnavailableError("tokenizer 无法提供可单调切分的子块边界")
        intervals.append((start + local_start, start + local_end))
        previous_end = local_end
        if token_end >= total and local_end >= len(parent_text):
            break
        next_token_start = token_start + step_tokens
        if token_end >= total:
            consumed_tokens = _token_count(tokenizer, parent_text[:local_end])
            token_start = max(token_start + 1, consumed_tokens)
        else:
            token_start = next_token_start
    return intervals


def _unescape_separator(separator: str) -> str:
    """将配置中的转义分隔符还原为实际文本。"""
    return separator.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t").replace("\\\\", "\\")


def _separator_end_before(text: str, start: int, end: int, separator: str) -> int | None:
    """查找 token 窗口内最后一个分隔符，并返回其后的边界。"""
    if not separator or end <= start:
        return None
    separator_start = text.rfind(separator, start, end)
    if separator_start < start:
        return None
    separator_end = separator_start + len(separator)
    if separator_end <= start or separator_end > end:
        return None
    return separator_end


def _offset_for_token_count(text: str, token_count: int, tokenizer: TokenizerLike) -> int:
    """查找达到指定 token 数的最小字符偏移。"""
    if token_count <= 0:
        return 0
    total = _token_count(tokenizer, text)
    if token_count >= total:
        return len(text)
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        if _token_count(tokenizer, text[:middle]) >= token_count:
            high = middle
        else:
            low = middle + 1
    return low


def _fit_end_for_token_limit(
    text: str,
    start: int,
    end: int,
    max_tokens: int,
    tokenizer: TokenizerLike,
) -> int:
    """将目标字符末端收缩到实际 token 上限内，避免 BPE 前缀漂移超限。"""
    if _token_count(tokenizer, text[start:end]) <= max_tokens:
        return end
    low, high = start + 1, end
    if _token_count(tokenizer, text[start:low]) > max_tokens:
        raise TokenizerUnavailableError("tokenizer 的单字符 token 已超过切块上限")
    while low < high:
        middle = (low + high + 1) // 2
        if _token_count(tokenizer, text[start:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return low


def _normalize_page_spans(
    page_spans: Sequence[PageInput] | None, text_length: int
) -> list[tuple[int, int, int | None]]:
    """校验页码区间并按起始 offset 排序。"""
    if page_spans is None:
        return []
    normalized: list[tuple[int, int, int | None]] = []
    for item in page_spans:
        if isinstance(item, Mapping):
            start, end = _read_interval(item, text_length, "页码 span")
            page_num = item.get("page_num")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 3:
            start, end = _read_interval({"start_offset": item[0], "end_offset": item[1]}, text_length, "页码 span")
            page_num = item[2]
        else:
            raise ValueError("页码 span 必须是对象或 (start_offset, end_offset, page_num)")
        if page_num is not None and (type(page_num) is not int or page_num < 1):
            raise ValueError("page_num 必须是正整数或 null")
        if normalized and start < normalized[-1][1]:
            raise ValueError("页码 span 存在重叠或未排序 offset")
        normalized.append((start, end, page_num))
    return normalized


def _build_spans(start: int, end: int, pages: Sequence[tuple[int, int, int | None]]) -> list[dict[str, int | None]]:
    """将子块区间映射为一个或多个页码 span，未映射部分使用 null 页码。"""
    if not pages:
        return [{"start_offset": start, "end_offset": end, "page_num": None}]
    spans: list[dict[str, int | None]] = []
    cursor = start
    for page_start, page_end, page_num in pages:
        if page_end <= cursor:
            continue
        if page_start >= end:
            break
        if cursor < page_start:
            gap_end = min(page_start, end)
            spans.append({"start_offset": cursor, "end_offset": gap_end, "page_num": None})
            cursor = gap_end
        overlap_start, overlap_end = max(cursor, page_start), min(end, page_end)
        if overlap_start < overlap_end:
            spans.append({"start_offset": overlap_start, "end_offset": overlap_end, "page_num": page_num})
            cursor = overlap_end
        if cursor >= end:
            break
    if cursor < end:
        spans.append({"start_offset": cursor, "end_offset": end, "page_num": None})
    return spans


def _stable_id(prefix: str, *parts: object) -> str:
    """依据稳定字段生成确定性 Parent-Child 标识。"""
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:32]}"
