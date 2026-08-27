from __future__ import annotations

import csv
import re
from typing import Any


def _rm_prefix(text: str) -> str:
    return re.sub(
        r"^(问题|答案|回答|user|assistant|Q|A|Question|Answer|问|答)[\t:： ]+",
        "",
        (text or "").strip(),
        flags=re.IGNORECASE,
    )


def _to_qa_chunk(question: str, answer: str, eng: bool = False) -> str:
    qprefix = "Question: " if eng else "问题："
    aprefix = "Answer: " if eng else "回答："
    return "\t".join([qprefix + _rm_prefix(question), aprefix + _rm_prefix(answer)])


def _guess_delimiter(lines: list[str]) -> str:
    comma = 0
    tab = 0
    for line in lines:
        if len(line.split(",")) == 2:
            comma += 1
        if len(line.split("\t")) == 2:
            tab += 1
    return "\t" if tab >= comma else ","


def _extract_pairs_with_delimiter(lines: list[str], delimiter: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    question = ""
    answer = ""

    for line in lines:
        arr = line.split(delimiter)
        if len(arr) != 2:
            if question:
                answer += "\n" + line
            continue

        if question and answer:
            pairs.append((question, answer))
        question, answer = arr

    if question:
        pairs.append((question, answer))

    return [(q.strip(), a.strip()) for q, a in pairs if q.strip()]


def _extract_pairs_from_csv(lines: list[str], delimiter: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    question = ""
    answer = ""

    reader = csv.reader(lines, delimiter=delimiter)
    for row, raw_line in zip(reader, lines, strict=False):
        if len(row) != 2:
            if question:
                answer += "\n" + raw_line
            continue

        if question and answer:
            pairs.append((question, answer))
        question, answer = row

    if question:
        pairs.append((question, answer))

    return [(q.strip(), a.strip()) for q, a in pairs if q.strip()]


def _parse_markdown_table_row(line: str) -> list[str] | None:
    if "|" not in line:
        return None

    text = line.strip()
    if not text:
        return None

    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]

    cells = [cell.strip() for cell in text.split("|")]
    if not cells:
        return None

    if all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells if c):
        return None

    return cells


def _extract_pairs_from_markdown_tables(markdown_content: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for line in (markdown_content or "").splitlines():
        cells = _parse_markdown_table_row(line)
        if not cells or len(cells) < 2:
            continue

        question = cells[0]
        answer = cells[1]
        if question and answer:
            pairs.append((question, answer))

    return pairs


def _md_question_level(line: str) -> tuple[int, str]:
    match = re.match(r"^#*", line)
    if not match:
        return 0, line
    return len(match.group(0)), line.lstrip("#").lstrip()


def _extract_pairs_from_markdown_headings(markdown_content: str) -> list[tuple[str, str]]:
    """标题提取：按 Markdown 标题层级提取问答对，标题下的内容作为答案，标题本身作为问题。"""
    lines = (markdown_content or "").splitlines()
    if not lines:
        return []

    pairs: list[tuple[str, str]] = []
    last_answer = ""
    question_stack: list[str] = []
    level_stack: list[int] = []
    code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            code_block = not code_block

        question_level = 0
        question = ""
        if not code_block:
            question_level, question = _md_question_level(line)

        if not question_level or question_level > 6:
            last_answer = f"{last_answer}\n{line}"
            continue

        if last_answer.strip():
            sum_question = "\n".join(question_stack)
            if sum_question:
                pairs.append((sum_question, last_answer.strip()))
            last_answer = ""

        while question_stack and question_level <= level_stack[-1]:
            question_stack.pop()
            level_stack.pop()

        question_stack.append(question)
        level_stack.append(question_level)

    if last_answer.strip():
        sum_question = "\n".join(question_stack)
        if sum_question:
            pairs.append((sum_question, last_answer.strip()))

    return pairs


def _extract_pairs_by_prefix(markdown_content: str) -> list[tuple[str, str]]:
    """前缀提取：按行首 Q/A 前缀提取问答对，标题行剥掉 # 后按前缀判断，非问答前缀标题仅作分节符结束当前问答对。"""
    pairs: list[tuple[str, str]] = []
    question = ""
    answer_lines: list[str] = []

    heading_re = re.compile(r"^#{1,6}\s*")
    question_re = re.compile(r"^(?:Q|Question|问|问题)\s*[:：]\s*(.*)$", flags=re.IGNORECASE)
    answer_re = re.compile(r"^(?:A|Answer|答|回答)\s*[:：]\s*(.*)$", flags=re.IGNORECASE)
    code_block = False

    def flush_pair() -> None:
        nonlocal question, answer_lines
        if question:
            pairs.append((question, "\n".join(answer_lines)))
            question = ""
            answer_lines = []

    for line in (markdown_content or "").splitlines():
        # 代码块内容（含围栏行）只可能是答案正文，不参与问答边界判断
        if line.strip().startswith("```"):
            code_block = not code_block
            if question:
                answer_lines.append(line)
            continue
        if code_block:
            if question:
                answer_lines.append(line)
            continue

        heading_match = heading_re.match(line)
        text = line[heading_match.end() :] if heading_match else line

        q_match = question_re.match(text)
        if q_match:
            flush_pair()
            question = q_match.group(1).strip()
            continue

        a_match = answer_re.match(text)
        if a_match:
            answer_lines.append(a_match.group(1).strip())
            continue

        if heading_match:
            # 非问答前缀的标题是结构性分节，结束当前问答对且标题本身不进入答案，避免附录等内容污染上一对问答
            flush_pair()
            continue

        if question:
            answer_lines.append(line)

    flush_pair()

    return [(q.strip(), a.strip()) for q, a in pairs if q.strip() and a.strip()]


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
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


# QA chunk 字符数硬上限：bge_m3 等模型限制 4096 tokens，中/英/数字混合内容按保守字符数兜底。
_QA_CHUNK_MAX_CHARS = 4000
_QA_QUESTION_PREFIXES = ("问题：", "Question: ")
_QA_ANSWER_PREFIXES = ("回答：", "Answer: ")


def _split_qa_prefix(text: str, prefixes: tuple[str, ...]) -> tuple[str, str]:
    """从 QA chunk 的半段文本中拆分出前缀和正文。"""
    for prefix in prefixes:
        if text.startswith(prefix):
            return prefix, text[len(prefix) :].strip()
    return "", text.strip()


def _hard_split_text(text: str, max_chars: int) -> list[str]:
    """按固定字符数硬切，过滤空白片段。"""
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars) if text[i : i + max_chars].strip()]


def _split_answer_by_paragraphs(answer: str, max_chars: int) -> list[str]:
    """优先按段落切分答案，单段落仍超长则按行、再超长则硬切。"""
    paragraphs = [p.strip() for p in answer.split("\n\n") if p.strip()]
    if not paragraphs:
        return [answer] if answer.strip() else []

    result: list[str] = []
    current = ""
    for p in paragraphs:
        if len(p) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.extend(_split_answer_by_lines(p, max_chars))
            continue
        if current and len(current) + 2 + len(p) > max_chars:
            result.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        result.append(current)
    return result


def _split_answer_by_lines(answer: str, max_chars: int) -> list[str]:
    """按行切分答案，单行仍超长则硬切。"""
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if not lines:
        return [answer] if answer.strip() else []

    result: list[str] = []
    current = ""
    for line in lines:
        if len(line) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.extend(_hard_split_text(line, max_chars))
            continue
        if current and len(current) + 1 + len(line) > max_chars:
            result.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        result.append(current)
    return result


def _split_long_qa_chunks(chunks: list[str], max_chars: int = _QA_CHUNK_MAX_CHARS) -> list[str]:
    """对超长 QA chunk 保留问题、切分答案，避免单条超过 embedding 上下文上限。"""
    if max_chars <= 0:
        return [c.strip() for c in chunks if c and c.strip()]

    result: list[str] = []
    for chunk in chunks:
        text = (chunk or "").strip()
        if not text:
            continue
        if len(text) <= max_chars:
            result.append(text)
            continue

        parts = text.split("\t", 1)
        if len(parts) != 2:
            # 非标准 QA 格式，直接硬切兜底
            result.extend(_hard_split_text(text, max_chars))
            continue

        q_part, a_part = parts
        q_prefix, q_body = _split_qa_prefix(q_part, _QA_QUESTION_PREFIXES)
        a_prefix, a_body = _split_qa_prefix(a_part, _QA_ANSWER_PREFIXES)
        if not q_body or not a_body:
            result.extend(_hard_split_text(text, max_chars))
            continue

        if len(q_prefix) + len(q_body) + len(a_prefix) + 1 >= max_chars:
            # 问题本身已超限，保留问题切答案只会产出 1 字符答案的碎片且仍超限，改为整条硬切
            result.extend(_hard_split_text(text, max_chars))
            continue

        # 预留问题部分 + 前缀 + 制表符占位
        max_answer_chars = max_chars - len(q_prefix) - len(q_body) - len(a_prefix) - 1
        for sub_answer in _split_answer_by_paragraphs(a_body, max_answer_chars):
            result.append(f"{q_prefix}{q_body}\t{a_prefix}{sub_answer}")

    return result


def chunk_markdown(filename: str, markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """QA 分块策略：按文件后缀选择下列提取器的子集，去重后统一渲染为 问题：xxx\\t回答：yyy 文本。

    提取器全集（按优先级编号；各后缀只走其中子集，见分支行内注释）：
    1. 行首 Q/A 前缀：按行首 Q/A 前缀切问答边界，标题行先剥 # 再匹配；`# Q:`/`# 问题:` 这类带前缀的标题被识别为问题，纯 `# 标题`（无 Q/问题 前缀）仅作分节符结束当前问答对、不进答案也不作问题。
    2. Markdown 标题：标题作问题、标题下内容作答案；仅当 1. 整轮未命中时兜底（用于纯 `# 标题` 风格的 FAQ 文档）。
    3. Markdown 表格：按 | 分隔两列表格作 Q/A 对；md/markdown/mdx/docx/csv/无后缀与 1./2. 叠加，xlsx 在 1./2. 落空后才尝试，txt 不走表格。
    4. 分隔符：按 tab/comma 切两列；csv 固定执行，xlsx/无后缀在前面落空时兜底，txt 中作为最高优先级先于 1.。
    5. 1-4 全部落空时，按每两行一组兜底构成问答对。
    6. 超长 chunk 保留问题、按段落/行逐级切分答案，避免单条超过 embedding 上下文上限。
    7. 问题本身超限等无法保留结构时，对超长 chunk 按字符数硬切并过滤空白片段，保证单条不超上限。
    """
    parser_config = parser_config or {}
    eng = str(parser_config.get("language", "Chinese")).lower() == "english"

    suffix = ""
    if filename and "." in filename:
        suffix = "." + filename.lower().split(".")[-1]

    lines = [line for line in (markdown_content or "").splitlines() if line.strip()]
    pairs: list[tuple[str, str]] = []

    # 各分支的提取器组合按后缀分发，编号对应 docstring 中的策略步骤
    if suffix in {".xlsx", ".xls"}:
        # 3. 表格提取，无命中退到 4. 分隔符提取
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
        if not pairs:
            delimiter = _guess_delimiter(lines)
            pairs.extend(_extract_pairs_with_delimiter(lines, delimiter))
    elif suffix == ".csv":
        # 3. 表格提取与 4. 分隔符（csv 解析）固定执行
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
        delimiter = "\t" if any("\t" in line for line in lines) else ","
        pairs.extend(_extract_pairs_from_csv(lines, delimiter))
    elif suffix == ".txt":
        # 4. 分隔符优先（兼容 Q: 问题\tA: 答案 整行格式），无命中退到 1. 行首前缀
        delimiter = _guess_delimiter(lines)
        pairs.extend(_extract_pairs_with_delimiter(lines, delimiter))
        if not pairs:
            pairs.extend(_extract_pairs_by_prefix(markdown_content))
    elif suffix in {".md", ".markdown", ".mdx", ".docx"}:
        # 1. 行首前缀优先；2. 前缀未命中时标题提取兜底；3. 表格提取叠加
        pairs.extend(_extract_pairs_by_prefix(markdown_content))
        if not pairs:
            pairs.extend(_extract_pairs_from_markdown_headings(markdown_content))
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
    else:
        # 1. 行首前缀 →（空）2. 标题提取兜底；3. 表格叠加；仍为空退到 4. 分隔符
        pairs.extend(_extract_pairs_by_prefix(markdown_content))
        if not pairs:
            pairs.extend(_extract_pairs_from_markdown_headings(markdown_content))
        pairs.extend(_extract_pairs_from_markdown_tables(markdown_content))
        if not pairs:
            delimiter = _guess_delimiter(lines)
            pairs.extend(_extract_pairs_with_delimiter(lines, delimiter))

    pairs = _dedupe_pairs(pairs)

    if not pairs and lines:
        # 5. 最后兜底：把内容按 2 行一组构成问答
        for i in range(0, len(lines), 2):
            q = lines[i]
            a = lines[i + 1] if i + 1 < len(lines) else ""
            if q.strip() and a.strip():
                pairs.append((q, a))

    chunks = [_to_qa_chunk(q, a, eng=eng) for q, a in pairs]
    # 6/7. 超长 chunk 限长：保留问题切答案，问题本身超限时整条硬切
    return _split_long_qa_chunks(chunks)
