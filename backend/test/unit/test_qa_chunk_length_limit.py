"""QA parser 超长 chunk 限长切分（_split_long_qa_chunks）的回归测试。

验收主张：chunk_markdown 产出的任意单条 chunk 不超过 _QA_CHUNK_MAX_CHARS，
避免超过 bge_m3 等 embedding 模型的 4096 token 上下文上限；切分时保留问题、
只切答案，保证每条 chunk 仍是完整问答语义。
"""

import importlib.util
from pathlib import Path

_PKG = Path(__file__).resolve().parents[2] / "package"


def _load_qa_parser():
    """按文件路径隔离加载 qa parser；其仅依赖标准库，无需注册 sys.modules。"""
    spec = importlib.util.spec_from_file_location(
        "qa_parser_under_test",
        _PKG / "yuxi/knowledge/chunking/ragflow_like/parsers/qa.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qa = _load_qa_parser()

# embedding 上下文上限的保守字符兜底（对应 bge_m3 4096 token），独立于实现常量，
# 避免用被测对象的默认值充当断言边界形成自我引用 oracle
_EMBEDDING_CHAR_LIMIT = 4000
_QUESTION = "问题：" + "这是一个问题" * 5  # 远低于上限，切分后可原样保留


def _long_chunk(answer_body: str) -> str:
    return f"{_QUESTION}\t回答：{answer_body}"


def _split(chunks: list[str]) -> list[str]:
    """显式传入限长值，单测只验证切分逻辑本身，不依赖实现默认常量。"""
    return qa._split_long_qa_chunks(chunks, max_chars=_EMBEDDING_CHAR_LIMIT)


class TestSplitLongQaChunks:
    def test_short_chunks_pass_through(self):
        chunks = ["问题：短问题\t回答：短答案", "Question: q\tAnswer: a"]
        assert _split(chunks) == [c.strip() for c in chunks]

    def test_blank_chunks_filtered(self):
        assert _split(["", "   ", "问题：q\t回答：a"]) == ["问题：q\t回答：a"]

    def test_long_answer_split_by_paragraphs_keeps_question(self):
        body = "\n\n".join(f"段落{i}内容。" * 300 for i in range(3))
        result = _split([_long_chunk(body)])
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= _EMBEDDING_CHAR_LIMIT
            # 每条子 chunk 保留完整问题与前缀，维持问答语义
            assert chunk.startswith(f"{_QUESTION}\t回答：")
        # 答案内容在切分结果中完整保留
        joined = "".join(c.split("\t回答：", 1)[1] for c in result)
        for i in range(3):
            assert f"段落{i}内容。" in joined

    def test_single_long_paragraph_falls_back_to_lines(self):
        body = "\n".join(f"第{i}行" + "内容" * 100 for i in range(30))
        result = _split([_long_chunk(body)])
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= _EMBEDDING_CHAR_LIMIT
            assert chunk.startswith(f"{_QUESTION}\t回答：")

    def test_line_split_preserves_code_indentation(self):
        # 缩进对围栏代码块有语义：按行切分不得剥离前导空白
        code = ["```python", "def handler():", "    if ready:", "        return compute()", "```"]
        filler = [f"说明行{i}：" + "内容" * 100 for i in range(30)]
        body = "\n".join(filler[:15] + code + filler[15:])
        result = _split([_long_chunk(body)])
        assert len(result) > 1
        joined = "\n".join(result)
        for line in code:
            assert line in joined

    def test_structureless_long_answer_hard_split(self):
        result = _split([_long_chunk("答" * 9000)])
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= _EMBEDDING_CHAR_LIMIT
            assert chunk.startswith(f"{_QUESTION}\t回答：")

    def test_oversized_question_falls_back_to_hard_split(self):
        # 问题本身已接近上限时，保留问题切答案只会产出 1 字符答案碎片，应整条硬切
        chunk = "问题：" + "超" * 5000 + "\t回答：答案"
        result = _split([chunk])
        assert len(result) > 1
        assert all(len(c) <= _EMBEDDING_CHAR_LIMIT for c in result)

    def test_non_standard_chunk_hard_split(self):
        result = _split(["无结构文本" * 1000])
        assert len(result) > 1
        assert all(len(c) <= _EMBEDDING_CHAR_LIMIT for c in result)

    def test_question_containing_tab_kept_whole(self):
        # 问题本身含 tab：结构分隔符是紧邻答案前缀的 tab，不是首个任意 tab
        question = "问题：什么是\t缩进风格？"
        result = _split([question + "\t回答：" + "答案内容。" * 1500])
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= _EMBEDDING_CHAR_LIMIT
            assert chunk.startswith(question + "\t回答：")

    def test_tab_chunk_without_answer_marker_hard_split(self):
        # 含 tab 但无已知答案前缀的 chunk 仍属非标准格式，硬切兜底
        result = _split(["左列\t右列" * 1000])
        assert len(result) > 1
        assert all(len(c) <= _EMBEDDING_CHAR_LIMIT for c in result)

    def test_zero_max_chars_only_strips(self):
        assert qa._split_long_qa_chunks(["  问题：q\t回答：a  "], max_chars=0) == ["问题：q\t回答：a"]


class TestChunkMarkdownLengthCap:
    """端到端验收：真实 QA 文档经 chunk_markdown 后任意 chunk 不超 embedding 上限。"""

    _LONG_MD = "Q: 高频问题\nA: " + "长答案内容。" * 1500

    def test_all_chunks_within_embedding_limit(self):
        # 走实现默认常量：锁定「默认上限不超 embedding 承诺」这一工程主张，
        # 若 _QA_CHUNK_MAX_CHARS 被调大到超过 4000，本断言会失败
        chunks = qa.chunk_markdown("faq.md", self._LONG_MD)
        assert len(chunks) > 1
        assert all(len(c) <= _EMBEDDING_CHAR_LIMIT for c in chunks)
        assert all(c.startswith("问题：高频问题\t回答：") for c in chunks)

    def test_input_actually_triggers_split(self):
        # 负向校验：同一输入若缺少限长步骤必然超限，证明上面的端到端断言能捕获 guard 回归
        raw = qa._to_qa_chunk("高频问题", "长答案内容。" * 1500)
        assert len(raw) > _EMBEDDING_CHAR_LIMIT
