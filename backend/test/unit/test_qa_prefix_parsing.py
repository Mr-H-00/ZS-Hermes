"""QA parser 前缀/标题提取的代码围栏边界回归测试。

验收主张：答案中的围栏代码块（``` 或 ~~~，同标记成对）是答案正文，
块内形似 Q:/A: 或 Markdown 标题的行不得被解析为真实问答边界。
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

_TILDE_MD = "Q: 如何配置？\nA: 参考示例：\n~~~\nQ: 注释里的文本\nA: 不是真实问答\n~~~\n完成后重启。"


class TestPrefixFenceBoundary:
    def test_tilde_fence_content_stays_in_answer(self):
        # 核心回归：tilde 围栏内的 Q:/A: 行不得拆出虚构问答对
        pairs = qa._extract_pairs_by_prefix(_TILDE_MD)
        assert len(pairs) == 1
        q, a = pairs[0]
        assert q == "如何配置？"
        assert "注释里的文本" in a
        assert "完成后重启。" in a

    def test_backtick_fence_content_stays_in_answer(self):
        pairs = qa._extract_pairs_by_prefix(_TILDE_MD.replace("~~~", "```"))
        assert len(pairs) == 1
        assert "注释里的文本" in pairs[0][1]

    def test_fence_with_info_string(self):
        md = "Q: 配置？\nA: 示例：\n~~~python\nQ: 注释\n~~~\n完。"
        pairs = qa._extract_pairs_by_prefix(md)
        assert len(pairs) == 1
        assert "Q: 注释" in pairs[0][1]

    def test_mismatched_fence_does_not_close_block(self):
        # ``` 行不关闭 ~~~ 块；块内 Q: 行仍属答案
        md = "Q: 配置？\nA: 开始\n~~~\n```\nQ: 仍在块内\n~~~\nQ: 真实问题2\nA: 答案2"
        pairs = qa._extract_pairs_by_prefix(md)
        assert len(pairs) == 2
        assert "仍在块内" in pairs[0][1]
        assert pairs[1] == ("真实问题2", "答案2")

    def test_unclosed_fence_absorbs_rest_into_answer(self):
        md = "Q: 配置？\nA: 开始\n~~~\nQ: 后面全在块内\nA: 也是"
        pairs = qa._extract_pairs_by_prefix(md)
        assert len(pairs) == 1
        assert "后面全在块内" in pairs[0][1]

    def test_heading_inside_tilde_fence_not_treated_as_question(self):
        # 标题提取路径：tilde 块内的 # 行不识别为标题
        md = "# 安装\n步骤一\n~~~\n# 注释不是标题\n~~~\n步骤二"
        pairs = qa._extract_pairs_from_markdown_headings(md)
        assert len(pairs) == 1
        q, a = pairs[0]
        assert q == "安装"
        assert "注释不是标题" in a
        assert "步骤二" in a

    def test_chunk_markdown_end_to_end_tilde_fence(self):
        chunks = qa.chunk_markdown("faq.md", _TILDE_MD)
        assert len(chunks) == 1
        assert chunks[0].startswith("问题：如何配置？\t回答：")
        assert "注释里的文本" in chunks[0]


class TestOrphanAnswer:
    """答案前缀行必须归属活跃问题，前言中的孤儿答案不得污染后续问答对。"""

    def test_orphan_answer_before_first_question_discarded(self):
        md = "Answer: 孤儿前言\n# Q: 真实问题\nA: 真实答案"
        pairs = qa._extract_pairs_by_prefix(md)
        assert pairs == [("真实问题", "真实答案")]

    def test_orphan_answer_between_pairs_discarded(self):
        # 上一对已结束（分节标题 flush）、新问题未出现时，孤儿答案同样忽略
        md = "Q: 问题一\nA: 答案一\n# 分节\nA: 孤儿\nQ: 问题二\nA: 答案二"
        pairs = qa._extract_pairs_by_prefix(md)
        assert pairs == [("问题一", "答案一"), ("问题二", "答案二")]

    def test_multiple_answer_lines_within_question_kept(self):
        # 活跃问题下的多个 A: 行仍全部归入答案（修复不改变正常路径）
        md = "Q: 问题\nA: 第一行\nA: 第二行"
        pairs = qa._extract_pairs_by_prefix(md)
        assert pairs == [("问题", "第一行\n第二行")]
