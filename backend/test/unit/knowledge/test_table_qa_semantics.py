"""表格解析与问答分块按真实内容和来源行验证。"""

import pytest
from openpyxl import Workbook

from yuxi.infrastructure.document_parsing.parser import parse
from yuxi.modules.knowledge.chunking.dispatcher import chunk_markdown


@pytest.mark.parametrize("rows", [[("Q1?", "A1")], [("Q1?", "A1"), ("Q2?", "A2")]])
async def test_xlsx_without_declared_header_keeps_every_question(tmp_path, rows):
    """Excel 第一行没有表头声明时也必须进入解析正文与问答。"""
    source = tmp_path / "faq.xlsx"
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(source)
    parsed = await parse(source, tmp_path / "parsed")
    markdown = parsed.markdown_path.read_text()
    records = chunk_markdown(markdown, "file", source.name, {"chunk_preset_id": "qa"})
    assert [record["content"] for record in records] == [f"问题：{question}\t回答：{answer}" for question, answer in rows]
    for record, (question, answer) in zip(records, rows, strict=True):
        line = markdown.splitlines()[record["start_line"] - 1]
        assert question in line and answer in line
        assert record["start_line"] == record["end_line"]


@pytest.mark.parametrize(
    "body,expected",
    [
        ('"A|B?",Correct', "问题：A|B?\t回答：Correct"),
        ('Q,"first\nsecond"', "问题：Q\t回答：first\nsecond"),
        ('Q,"<br> & **literal**"', "问题：Q\t回答：<br> & **literal**"),
        ('Q,"C:\\path\\file | `code`"', "问题：Q\t回答：C:\\path\\file | `code`"),
        ("Which?,A bird", "问题：Which?\t回答：A bird"),
        ("Question of time?,A | B", "问题：Question of time?\t回答：A | B"),
        ("00123,00456", "问题：00123\t回答：00456"),
        ("code,NA", "问题：code\t回答：NA"),
        ("true,false", "问题：true\t回答：false"),
    ],
)
async def test_csv_cells_keep_delimiters_newlines_and_literal_text(tmp_path, body, expected):
    """CSV 字段内容不能成为 Markdown 列边界或丢失的后续行。"""
    source = tmp_path / "faq.csv"
    source.write_text(f"question,answer\n{body}\n")
    parsed = await parse(source, tmp_path / "parsed")
    markdown = parsed.markdown_path.read_text()
    records = chunk_markdown(markdown, "file", source.name, {"chunk_preset_id": "qa"})
    assert [record["content"] for record in records] == [expected]
    assert records[0]["start_line"] == records[0]["end_line"] == 3


@pytest.mark.parametrize(
    "markdown,expected",
    [
        ("| question | answer |\n|---|---|\n| A\\|B? | Correct |", "问题：A|B?\t回答：Correct"),
        ("> | question | answer |\n> |---|---|\n> | Q? | Correct |", "问题：Q?\t回答：Correct"),
        ("| question | answer |\n|---|---|\n| **Q?** | A &#124; B<br>next |", "问题：Q?\t回答：A | B\nnext"),
    ],
)
def test_markdown_table_qa_consumes_cells_and_keeps_source_row(markdown, expected):
    """表格 token 拥有列语义，来源范围仍属于完整 Markdown 数据行。"""
    records = chunk_markdown(markdown, "file", "faq.md", {"chunk_preset_id": "qa"})
    assert [record["content"] for record in records] == [expected]
    assert records[0]["start_line"] == records[0]["end_line"] == 3
