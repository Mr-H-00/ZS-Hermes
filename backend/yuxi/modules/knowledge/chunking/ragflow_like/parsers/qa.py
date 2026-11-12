"""问答（QA）分块：从行首前缀、标题、表格与分隔符结构提取问答对，渲染为统一 QA 文本并做超长保护。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from markdown_it import MarkdownIt

from yuxi.modules.knowledge.chunking.source_spans import LocatedText, SourceText, join_source_text

# ATX 标题行：group(1) 为井号层级，正文自匹配末尾起
_HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t]+|$)")
# 行首问答标记：group(1) 为剥掉标记后的正文
_QUESTION_LINE_RE = re.compile(r"^(?:Q|Question|问|问题)\s*[:：]\s*(.*)$", flags=re.IGNORECASE)
_ANSWER_LINE_RE = re.compile(r"^(?:A|Answer|答|回答)\s*[:：]\s*(.*)$", flags=re.IGNORECASE)
# 半段文本自带的问答标记（含中英文写法），渲染前统一剥掉
_LABEL_PREFIX_RE = re.compile(r"^(问题|答案|回答|user|assistant|Q|A|Question|Answer|问|答)\s*[:：]\s*", flags=re.IGNORECASE)

# QA chunk 字符数硬上限：bge_m3 等模型限制 4096 tokens，中/英/数字混合内容按保守字符数兜底。
_QA_CHUNK_MAX_CHARS = 4000
_QA_QUESTION_PREFIXES = ("问题：", "Question: ")
_QA_ANSWER_PREFIXES = ("回答：", "Answer: ")


def chunk_markdown(filename: str, markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """从统一 Markdown 中提取问答，保留来源位置并切分超长答案。

    表格文件只读取 Markdown 表体；文本文件优先读取分隔列，其他文档优先读取
    Q/A 前缀或标题，并叠加表格问答。非表格文件在没有结构时按相邻两行兜底。
    围栏代码保持答案正文，超长问题退回字符切分以满足 embedding 上限。
    """
    parser_config = parser_config or {}
    markdown_content = SourceText(markdown_content or "")
    eng = str(parser_config.get("language", "Chinese")).lower() == "english"

    suffix = ""
    if filename and "." in filename:
        suffix = "." + filename.lower().split(".")[-1]

    lines = [line for line in markdown_content.splitlines() if line.strip()]
    pairs: list[tuple[str, str]] = []

    # 原始格式只决定提取优先级，输入始终是已解析的 Markdown。
    if suffix in {".xlsx", ".xls", ".csv"}:
        # 表格来源已经过统一文档转换，只消费 Markdown 表体。
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
    elif suffix == ".txt":
        # 分隔符优先（兼容 Q: 问题\tA: 答案 整行格式），无命中退到行首前缀。
        delimiter = _guess_delimiter(lines)
        pairs.extend(_extract_pairs_with_delimiter(lines, delimiter))
        if not pairs:
            pairs.extend(_extract_pairs_by_prefix(markdown_content))
    elif suffix in {".md", ".markdown", ".mdx", ".docx"}:
        # 行首前缀优先，未命中时标题提取兜底，并叠加表格。
        pairs.extend(_extract_pairs_by_prefix(markdown_content))
        if not pairs:
            pairs.extend(_extract_pairs_from_markdown_headings(markdown_content))
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
    else:
        # 前缀或标题提取后叠加表格；仍为空时再尝试分隔符。
        pairs.extend(_extract_pairs_by_prefix(markdown_content))
        if not pairs:
            pairs.extend(_extract_pairs_from_markdown_headings(markdown_content))
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
        if not pairs:
            delimiter = _guess_delimiter(lines)
            pairs.extend(_extract_pairs_with_delimiter(lines, delimiter))

    pairs = _dedupe_pairs(pairs)

    if not pairs and lines and suffix not in {".xlsx", ".xls", ".csv"}:
        # 没有结构时，把内容按 2 行一组构成问答。
        for i in range(0, len(lines), 2):
            question = lines[i]
            answer = lines[i + 1] if i + 1 < len(lines) else ""
            if question.strip() and answer.strip():
                pairs.append((question, answer))

    result: list[str] = []
    for question, answer in pairs:
        chunk = _to_qa_chunk(question, answer, eng=eng)
        # 保留问题切分超长答案，问题本身超限时整条硬切；片段继承原 chunk 来源区间。
        parts = _split_long_qa_chunks([chunk])
        if isinstance(chunk, LocatedText):
            result.extend(LocatedText(str(part), chunk.source_range) for part in parts)
        else:
            result.extend(parts)
    return result


# ---------- 问答对提取器 ----------


def _extract_pairs_by_prefix(markdown_content: str) -> list[tuple[str, str]]:
    """前缀提取：按行首 Q/A 前缀提取问答对，标题行剥掉 # 后按前缀判断，非问答前缀标题仅作分节符结束当前问答对。"""
    pairs: list[tuple[str, str]] = []
    question = ""
    answer_lines: list[str] = []
    fence = ""

    def flush_pair() -> None:
        nonlocal question, answer_lines
        if question:
            pairs.append((question, join_source_text(answer_lines, "\n")))
            question = ""
            answer_lines = []

    for line in (markdown_content or "").splitlines():
        stripped = line.strip()
        is_fence_line = stripped.startswith("```") or stripped.startswith("~~~")
        fence = _update_fence_state(line, fence)
        # 围栏行与代码块内容行只可能是答案正文，不参与问答边界判断
        if is_fence_line or fence:
            if question:
                answer_lines.append(line)
            continue

        heading_match = _HEADING_RE.match(line)
        text = line[heading_match.end() :] if heading_match else line

        q_match = _QUESTION_LINE_RE.match(text)
        if q_match:
            flush_pair()
            question = text[q_match.start(1) : q_match.end(1)].strip()
            continue

        a_match = _ANSWER_LINE_RE.match(text)
        if a_match:
            # 答案必须归属活跃问题：问题尚未出现时的前言 A: 行直接忽略，避免孤儿文本被拼进后续真实问答对
            if question:
                answer_lines.append(text[a_match.start(1) : a_match.end(1)].strip())
            continue

        if heading_match:
            # 非问答前缀的标题是结构性分节，结束当前问答对且标题本身不进入答案，避免附录等内容污染上一对问答
            flush_pair()
            continue

        if question:
            answer_lines.append(line)

    flush_pair()

    return [(q.strip(), a.strip()) for q, a in pairs if q.strip() and a.strip()]


def _update_fence_state(line: str, fence: str) -> str:
    """按行更新代码围栏状态：``` 与 ~~~ 各自成对开关，异类围栏行不关闭当前块。"""
    stripped = line.strip()
    if stripped.startswith("```") or stripped.startswith("~~~"):
        marker = stripped[:3]
        if not fence:
            return marker
        if marker == fence:
            return ""
    return fence


def _md_question_level(line: str) -> tuple[int, str]:
    """识别 ATX 标题行，返回 (层级, 标题正文)；非标题行返回 (0, 原行)。"""
    match = _HEADING_RE.match(line)
    if not match:
        return 0, line
    return len(match.group(1)), line[match.end() :]


def _extract_pairs_from_markdown_headings(markdown_content: str) -> list[tuple[str, str]]:
    """标题提取：按 Markdown 标题层级提取问答对，标题下的内容作为答案，标题本身作为问题。"""
    pairs: list[tuple[str, str]] = []
    question_stack: list[str] = []
    level_stack: list[int] = []
    last_answer = ""
    fence = ""

    def flush_pair() -> None:
        """落盘当前标题路径与累积答案；多级标题按层级栈拼成完整问题路径。"""
        nonlocal last_answer
        if not last_answer.strip():
            return
        question_path = join_source_text(question_stack, "\n")
        if question_path:
            pairs.append((question_path, last_answer.strip()))
        last_answer = ""

    for line in (markdown_content or "").splitlines():
        fence = _update_fence_state(line, fence)

        question_level = 0
        question = ""
        if not fence:
            question_level, question = _md_question_level(line)

        if not question_level:
            last_answer = join_source_text([last_answer, line], "\n")
            continue

        flush_pair()
        while question_stack and question_level <= level_stack[-1]:
            question_stack.pop()
            level_stack.pop()

        question_stack.append(question)
        level_stack.append(question_level)

    flush_pair()
    return pairs


def _extract_pairs_from_markdown_tables(markdown_content: str) -> list[tuple[str, str]]:
    """读取 Markdown 表体单元格语义，以数据行范围保留重写正文的来源。"""
    pairs: list[tuple[str, str]] = []
    in_body = False
    cells = []
    row_range = None
    for token in MarkdownIt("commonmark").enable("table").parse(markdown_content):
        if token.type == "tbody_open":
            in_body = True
        elif token.type == "tbody_close":
            in_body = False
        elif in_body and token.type == "tr_open":
            cells = []
            row_range = tuple(token.map)
        elif in_body and token.type == "inline":
            parts = []
            for child in token.children or []:
                if child.type in {"softbreak", "hardbreak"} or (
                    child.type == "html_inline" and re.fullmatch(r"<br\s*/?>", child.content, flags=re.IGNORECASE)
                ):
                    parts.append("\n")
                else:
                    parts.append(child.content)
            cells.append("".join(parts).strip())
        elif in_body and token.type == "tr_close" and len(cells) >= 2 and cells[0] and cells[1]:
            pairs.append((LocatedText(cells[0], row_range), cells[1]))
    return pairs


def _guess_delimiter(lines: list[str]) -> str:
    """统计恰好含一个逗号/制表符的行数，tab 行不少于逗号行时优先 tab。"""
    comma_lines = sum(1 for line in lines if line.count(",") == 1)
    tab_lines = sum(1 for line in lines if line.count("\t") == 1)
    return "\t" if tab_lines >= comma_lines else ","


def _extract_pairs_with_delimiter(lines: list[str], delimiter: str) -> list[tuple[str, str]]:
    """分隔符提取：按单分隔符切两列；列数不符的行并入当前答案，成对问答在下一对出现时落盘。"""
    pairs: list[tuple[str, str]] = []
    question = ""
    answer = ""

    for line in lines:
        parts = line.split(delimiter)
        if len(parts) != 2:
            if question:
                answer += "\n" + line
            continue

        if question and answer:
            pairs.append((question, answer))
        question, answer = parts

    if question:
        pairs.append((question, answer))

    return [(q.strip(), a.strip()) for q, a in pairs if q.strip()]


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """按 (问题, 答案) 去重并丢弃空半段，保持首次出现顺序。"""
    res: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for question, answer in pairs:
        q = question.strip()
        a = answer.strip()
        if not q or not a:
            continue
        key = (q, a)
        if key in seen:
            continue
        seen.add(key)
        res.append((q, a))

    return res


# ---------- 问答对渲染 ----------


def _rm_prefix(text: str) -> str:
    """剥掉半段文本行首自带的问答标记前缀，避免渲染后出现双重前缀。"""
    cleaned = text.strip()
    match = _LABEL_PREFIX_RE.match(cleaned)
    return cleaned[match.end() :] if match else cleaned


def _to_qa_chunk(question: str, answer: str, eng: bool = False) -> str:
    """渲染问答对为「问题：…\\t回答：…」文本；问题携带来源行区间时为产物附加同一区间。"""
    question_prefix = "Question: " if eng else "问题："
    answer_prefix = "Answer: " if eng else "回答："
    content = join_source_text([question_prefix + _rm_prefix(question), answer_prefix + _rm_prefix(answer)], "\t")
    if isinstance(question, LocatedText):
        return LocatedText(str(content), question.source_range)
    return content


# ---------- 超长 chunk 限长 ----------


def _split_long_qa_chunks(chunks: list[str], max_chars: int = _QA_CHUNK_MAX_CHARS) -> list[str]:
    """对超长 QA chunk 保留问题、切分答案，避免单条超过 embedding 上下文上限。"""
    if max_chars <= 0:
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    result: list[str] = []
    for chunk in chunks:
        text = chunk.strip()
        if text:
            result.extend(_split_single_chunk(text, max_chars))
    return result


def _split_single_chunk(text: str, max_chars: int) -> list[str]:
    """单条 chunk 限长：不超限原样返回；超限时优先保留问题切分答案，无法保留结构则整条硬切。"""
    if len(text) <= max_chars:
        return [text]

    # 分隔符必须与序列化问题的语言一致，避免问题正文中的异语言答案标记被误认成结构边界
    if text.startswith("问题："):
        marker = "\t回答："
    elif text.startswith("Question: "):
        marker = "\tAnswer: "
    else:
        marker = ""
    sep_pos = text.find(marker) if marker else -1
    if sep_pos == -1:
        # 非标准 QA 格式，直接硬切兜底
        return _hard_split_text(text, max_chars)

    # marker 以结构制表符开头，+1 跳过它即得到答案半段
    q_part, a_part = text[:sep_pos], text[sep_pos + 1 :]
    q_prefix, q_body = _split_qa_prefix(q_part, _QA_QUESTION_PREFIXES)
    a_prefix, a_body = _split_qa_prefix(a_part, _QA_ANSWER_PREFIXES)
    if not q_body or not a_body:
        return _hard_split_text(text, max_chars)

    if len(q_prefix) + len(q_body) + len(a_prefix) + 1 >= max_chars:
        # 问题本身已超限，保留问题切答案只会产出 1 字符答案的碎片且仍超限，改为整条硬切
        return _hard_split_text(text, max_chars)

    # 总预算扣除问题正文、两个前缀与结构制表符后全部留给答案
    max_answer_chars = max_chars - len(q_prefix) - len(q_body) - len(a_prefix) - 1
    return [
        join_source_text([q_prefix, q_body, "\t", a_prefix, sub_answer])
        for sub_answer in _split_answer_by_paragraphs(a_body, max_answer_chars)
    ]


def _split_qa_prefix(text: str, prefixes: tuple[str, ...]) -> tuple[str, str]:
    """从 QA chunk 的半段文本中拆分出前缀和正文。"""
    for prefix in prefixes:
        if text.startswith(prefix):
            return prefix, text[len(prefix) :].strip()
    return "", text.strip()


def _split_answer_by_paragraphs(answer: str, max_chars: int) -> list[str]:
    """优先按段落切分答案，单段落仍超长则按行、再超长则硬切。"""
    paragraphs = [p.strip() for p in answer.split("\n\n") if p.strip()]
    if not paragraphs:
        return [answer] if answer.strip() else []
    return _pack_segments(paragraphs, max_chars, "\n\n", _split_answer_by_lines)


def _split_answer_by_lines(answer: str, max_chars: int) -> list[str]:
    """按行切分答案，单行仍超长则硬切。"""
    # 仅 strip 用于判空，保留原始行：缩进对围栏代码块与嵌套列表有语义
    lines = [line for line in answer.splitlines() if line.strip()]
    if not lines:
        return [answer] if answer.strip() else []
    return _pack_segments(lines, max_chars, "\n", _hard_split_text)


def _pack_segments(segments: list[str], max_chars: int, separator: str, split_overlong: Callable[[str, int], list[str]]) -> list[str]:
    """贪心打包片段：装不下先落盘当前段，单段本身超限交给更细粒度的切分函数。"""
    result: list[str] = []
    current = ""
    for segment in segments:
        if len(segment) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.extend(split_overlong(segment, max_chars))
            continue
        if current and len(current) + len(separator) + len(segment) > max_chars:
            result.append(current)
            current = segment
        else:
            current = join_source_text([current, segment], separator) if current else segment
    if current:
        result.append(current)
    return result


def _hard_split_text(text: str, max_chars: int) -> list[str]:
    """按固定字符数硬切，过滤空白片段。"""
    parts = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    return [part for part in parts if part.strip()]
