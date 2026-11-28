"""将切分结果定位到入库前保存的完整文本。"""

from __future__ import annotations

import re
from array import array
from bisect import bisect_right
from collections.abc import Iterable, Sequence


class SourceText(str):
    """随文本切分、合并保留字符来源；生成字符用 -1 标记。"""

    def __new__(cls, content: str, positions: Sequence[int] | None = None):
        """原文默认使用连续字符位置，子片段继承已有映射。"""
        instance = super().__new__(cls, content)
        instance.source_positions = positions if positions is not None else range(len(content))
        return instance

    def __getitem__(self, key):
        """切片时同时切分字符位置，单字符读取维持 str 行为。"""
        content = super().__getitem__(key)
        return SourceText(content, self.source_positions[key]) if isinstance(key, slice) else content

    def strip(self, chars=None):
        """清理首尾空白时保留正文位置。"""
        start = len(self) - len(super().lstrip(chars))
        end = len(super().rstrip(chars))
        return self[start:end]

    def splitlines(self, keepends=False):
        """按原文行切分，保留空行和换行后的绝对位置。"""
        lines = []
        offset = 0
        for line in super().splitlines(keepends=True):
            part = self[offset : offset + len(line)]
            body_length = len(line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"))
            lines.append(part if keepends else part[:body_length])
            offset += len(line)
        return lines

    def split(self, sep=None, maxsplit=-1):
        """分隔文本时保留每个子串的来源。"""
        result = []
        offset = 0
        for part in super().split(sep, maxsplit):
            start = str(self).find(part, offset)
            result.append(self[start : start + len(part)])
            offset = start + len(part) + (len(sep) if sep else 0)
        return result

    def __add__(self, other):
        """追加文本时保留来源，生成前缀不拥有原文位置。"""
        return join_source_text([self, other])

    def __radd__(self, other):
        """为前置的生成前缀与正文合并来源。"""
        return join_source_text([other, self])

    def map_fragment(self, content: str, start: int = 0) -> tuple[SourceText, int]:
        """定位只删减排版字符的连续子片段，空白重排不推进正文。"""
        positions = array("q")
        cursor = start
        for char in content:
            if char.isspace() and (cursor >= len(self) or self[cursor] != char):
                positions.append(-1)
                continue
            found = self.find(char, cursor)
            if found < 0:
                raise ValueError("切分正文无法映射到其原始文本范围")
            positions.append(self.source_positions[found])
            cursor = found + 1
        return SourceText(content, positions), cursor


def join_source_text(parts: Iterable[str], separator: str = "") -> str:
    """合并普通或带来源文本，并为新增分隔符保留空映射。"""
    parts = list(parts)
    content = separator.join(parts)
    if not any(isinstance(part, SourceText) for part in parts):
        return content
    positions = array("q")
    for index, part in enumerate(parts):
        if index:
            positions.extend([-1] * len(separator))
        positions.extend(part.source_positions if isinstance(part, SourceText) else [-1] * len(part))
    return SourceText(content, positions)


class LocatedText(str):
    """为会重写正文的切分器保留 Markdown token 的来源行区间。"""

    def __new__(cls, content: str, source_range: tuple[int, int]):
        """携带正文来源，不改变字符串的消费契约。"""
        instance = super().__new__(cls, content)
        instance.source_range = source_range
        return instance

    def strip(self, chars=None):
        """裁剪重写正文时保留原始块范围。"""
        return LocatedText(super().strip(chars), self.source_range)


class SourceSpanLocator:
    """把切分器保留的字符或 token 来源投影为原文行号。"""

    def __init__(self, source: str):
        """记录换行与正文字符的原始位置。"""
        self.source = source
        self.line_starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", source))]

    def locate(self, text: str, *, source_range: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
        """返回字符半开区间与 1-based 首尾行号。"""
        if isinstance(text, SourceText):
            positions = [position for position in text.source_positions if position >= 0]
            if not positions:
                raise ValueError("Chunk 缺少可定位的原文内容")
            start, end = min(positions), max(positions) + 1
        elif source_range is not None:
            # HTML 表格、数学块等重写内容使用 parser 的 token.map，避免文本反查猜测。
            first, after_last = source_range
            start = self.line_starts[first]
            end = self.line_starts[after_last] if after_last < len(self.line_starts) else len(self.source)
        else:
            raise ValueError("Chunk 未保留切分来源位置，无法定位到解析原文")
        return start, end, bisect_right(self.line_starts, start), bisect_right(self.line_starts, end - 1)
