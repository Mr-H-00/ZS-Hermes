"""markdown-it token 流的表格块提取与层次化长文切分辅助。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from yuxi.modules.knowledge.chunking.ragflow_like.utils.semantic_utils import semantic_chunking_with_auto_clusters


def get_title_path(stack: list[str]) -> str:
    """按标题栈生成以“|”分隔的标题路径。"""
    return "|".join([t for t in stack if t])


def extract_table_block(tokens: list[Any], i: int, original_lines: list[str]) -> tuple[int, str]:
    """
    从token流和原始文本中提取完整的表格块。

    逻辑说明：
    1. 定位起始：通过当前 token (i) 的 `map` 属性获取表格在原始行中的起始行号 `table_start`。
    2. 查找结束 token：遍历后续 tokens 直到找到 `table_close`。
    3. 确定结束行号 (`table_end`)：
       - 优先使用 `table_close` token 的 `map` 属性。
       - 若不存在，则尝试查找下一个带有 `map` 信息的 token 的起始行作为当前表格的结束。
       - 若上述均失败（如文件末尾或解析异常），则回退到基于文本内容的启发式扫描。
    4. 返回结果：返回 `table_close` 的索引 `j` 以及拼接后的表格原始字符串。
    """
    token = tokens[i]
    table_start = token.map[0] if token.map else 0
    j = i + 1
    while j < len(tokens) and tokens[j].type != "table_close":
        j += 1
    if j < len(tokens):
        end_token = tokens[j]
        if end_token.map and end_token.map[1] is not None:
            table_end = end_token.map[1]
        else:
            table_end = None
            for k in range(j + 1, len(tokens)):
                if tokens[k].map and tokens[k].map[0] is not None:
                    table_end = tokens[k].map[0]
                    break
            if table_end is None:
                table_end = _scan_table_end_by_text(original_lines, table_start)
    else:
        table_end = _scan_table_end_by_text(original_lines, table_start)
    return j, "\n".join(original_lines[table_start:table_end])


def split_text_by_length_and_newline(
    text: str, max_length: int, embed_fn: Callable[[list[str]], Any] | None, token_count_fn: Callable[[str], int]
) -> list[str]:
    """段落 → 行 → 语义三层递进的超长文本切分。"""
    chunks = []

    paragraphs = text.split("\n\n")

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        paragraph_token_count = token_count_fn(paragraph)
        if paragraph_token_count <= max_length:
            chunks.append(paragraph)
            continue

        # 段落超限时按行累积切分；单行超限再交给语义切分兜底。
        lines = paragraph.split("\n")
        current_chunk_lines = []
        current_chunk_tokens = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue

            line_token_count = token_count_fn(line)
            # 行与行之间以换行拼接，非首行需要额外计 1 个 token。
            added_tokens = line_token_count + (1 if current_chunk_lines else 0)
            if line_token_count > max_length:
                if current_chunk_lines:
                    chunks.append("\n".join(current_chunk_lines))
                    current_chunk_lines = []
                    current_chunk_tokens = 0

                sub_chunks = semantic_chunking_with_auto_clusters(
                    line,
                    embed_fn=embed_fn,
                    token_count_fn=token_count_fn,
                    max_chunk_size=max_length,
                )
                chunks.extend(sub_chunks)
            elif current_chunk_tokens + added_tokens > max_length:
                chunks.append("\n".join(current_chunk_lines))
                current_chunk_lines = [line]
                current_chunk_tokens = line_token_count
            else:
                current_chunk_lines.append(line)
                current_chunk_tokens += added_tokens
        if current_chunk_lines:
            chunks.append("\n".join(current_chunk_lines))

    return chunks


def _scan_table_end_by_text(original_lines: list[str], table_start: int) -> int:
    """无 token.map 可用时，按 Markdown 表格行特征启发式扫描结束行号。"""
    for line_idx in range(table_start, len(original_lines)):
        line = original_lines[line_idx].strip()
        if not line or not (line.startswith("|") or "|" in line):
            return line_idx
    return table_start + 1
