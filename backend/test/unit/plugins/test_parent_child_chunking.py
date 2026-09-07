"""Parent-Child 切块单元测试。"""

from __future__ import annotations

import importlib

import pytest

from yuxi.knowledge.chunking.ragflow_like.parent_child import (
    TokenizerUnavailableError,
    chunk_parent_child,
    resolve_tokenizer,
)


class CharacterTokenizer:
    """以每个非空白字符为一个 token 的确定性测试 tokenizer。"""

    def encode(self, text: str) -> list[str]:
        """返回非空白字符 token。"""
        return [char for char in text if not char.isspace()]


def _params(**overrides: object) -> dict:
    """构造测试用 Parent-Child 参数。"""
    config = {
        "enabled": True,
        "parent_token_num": 256,
        "child_token_num": 64,
        "child_overlap_percent": 25,
    }
    config.update(overrides)
    return {"parent_child": config}


def test_parent_child_preserves_raw_text_offsets_and_ids() -> None:
    """父块和子块正文必须直接回读自原文，且 offset 可切回相同内容。"""
    text = "甲乙丙\n\n" + "丁戊己庚辛" * 60
    result = chunk_parent_child(
        text,
        "file-1",
        "kb-1",
        "version-1",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=0),
        tokenizer=CharacterTokenizer(),
    )

    assert result["parents"]
    assert result["children"]
    for parent in result["parents"]:
        assert parent["parent_text"] == text[parent["start_offset"] : parent["end_offset"]]
        assert parent["token_count"] <= 256
    for child in result["children"]:
        assert child["child_text"] == text[child["start_offset"] : child["end_offset"]]
        assert child["token_count"] <= 64
        assert child["parent_id"].startswith("parent_")
        assert child["child_id"].startswith("child_")


def test_child_overlap_is_token_based_and_stays_inside_parent() -> None:
    """子块重叠按 floor 规则计算，并且不能跨出父块区间。"""
    text = "甲乙丙丁戊己庚" * 20
    result = chunk_parent_child(
        text,
        "file-2",
        "kb-2",
        "version-2",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=50),
        tokenizer=CharacterTokenizer(),
    )

    children = result["children"]
    assert len(children) >= 2
    assert children[0]["end_offset"] > children[1]["start_offset"]
    parent = result["parents"][0]
    assert all(
        parent["start_offset"] <= child["start_offset"] < child["end_offset"] <= parent["end_offset"]
        for child in children
    )


def test_child_separator_preferred_boundary_changes_child_intervals() -> None:
    """自定义分隔符应改变子块边界，同时保持原文 offset 和 token 上限。"""
    text = "|".join(["a" * 50, "b" * 50, "c" * 50, "d" * 50])
    result = chunk_parent_child(
        text,
        "file-separator",
        "kb-separator",
        "version-separator",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=0, separator="|"),
        tokenizer=CharacterTokenizer(),
    )

    children = result["children"]
    assert len(children) == 4
    assert [child["child_text"] for child in children] == [
        "a" * 50 + "|",
        "b" * 50 + "|",
        "c" * 50 + "|",
        "d" * 50,
    ]
    assert all(child["child_text"] == text[child["start_offset"] : child["end_offset"]] for child in children)
    assert all(child["token_count"] <= 64 for child in children)


def test_child_separator_with_overlap_preserves_parent_tail() -> None:
    """分隔符提前收束窗口时，最后一个子块仍必须覆盖父块尾部。"""
    text = "|".join(["a" * 50, "b" * 50, "c" * 50, "d" * 50])
    result = chunk_parent_child(
        text,
        "file-separator-overlap",
        "kb-separator-overlap",
        "version-separator-overlap",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=25, separator="|"),
        tokenizer=CharacterTokenizer(),
    )

    parent = result["parents"][0]
    children = result["children"]
    assert children[0]["start_offset"] == parent["start_offset"]
    assert all(
        current["start_offset"] <= previous["end_offset"]
        for previous, current in zip(children, children[1:], strict=False)
    )
    assert children[-1]["end_offset"] == parent["end_offset"]
    assert text[children[-1]["start_offset"] : children[-1]["end_offset"]].endswith("d" * 50)


def test_default_newline_separator_with_overlap_preserves_parent_tail() -> None:
    """默认换行分隔符与 overlap 组合时，子块联合范围必须覆盖父块。"""
    text = "\n".join(["a" * 40, "b" * 40, "c" * 40, "d" * 40])
    result = chunk_parent_child(
        text,
        "file-newline-overlap",
        "kb-newline-overlap",
        "version-newline-overlap",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=15),
        tokenizer=CharacterTokenizer(),
    )

    parent = result["parents"][0]
    children = result["children"]
    assert children[0]["start_offset"] == parent["start_offset"]
    assert all(
        current["start_offset"] <= previous["end_offset"]
        for previous, current in zip(children, children[1:], strict=False)
    )
    assert children[-1]["end_offset"] == parent["end_offset"]


def test_cross_page_child_creates_multiple_spans() -> None:
    """跨页子块应保留每个页码对应的原文区间。"""
    text = "甲乙\n丙丁\n" + "戊己" * 50
    result = chunk_parent_child(
        text,
        "file-page",
        "kb-page",
        "version-page",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=0),
        tokenizer=CharacterTokenizer(),
        page_spans=[
            {"start_offset": 0, "end_offset": 5, "page_num": 1},
            {"start_offset": 5, "end_offset": len(text), "page_num": 2},
        ],
    )

    child = result["children"][0]
    assert [span["page_num"] for span in child["spans"]] == [1, 2]
    assert child["spans"][0]["start_offset"] == child["start_offset"]
    assert child["spans"][-1]["end_offset"] == child["end_offset"]
    assert "\n".join(text[span["start_offset"] : span["end_offset"]] for span in child["spans"]) != ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"parent_token_num": 64, "child_token_num": 64},
        {"parent_token_num": 255},
        {"child_token_num": 63},
        {"child_overlap_percent": 100},
        {"child_overlap_percent": 1.5},
        {"separator": ""},
    ],
)
def test_invalid_parent_child_parameters_fail_closed(overrides: dict[str, object]) -> None:
    """非法父子参数必须直接拒绝，不能静默使用默认值。"""
    with pytest.raises(ValueError):
        chunk_parent_child(
            "甲乙丙丁",
            "file-invalid",
            "kb-invalid",
            "version-invalid",
            _params(**overrides),
            tokenizer=CharacterTokenizer(),
        )


def test_invalid_parser_offsets_fail_closed() -> None:
    """解析器提供越界或文本不一致的 offset 时必须拒绝。"""
    with pytest.raises(ValueError, match="offset"):
        chunk_parent_child(
            "甲乙丙",
            "file-offset",
            "kb-offset",
            "version-offset",
            _params(),
            parsed_segments=[{"start_offset": 0, "end_offset": 99}],
            tokenizer=CharacterTokenizer(),
        )

    with pytest.raises(ValueError, match="不一致"):
        chunk_parent_child(
            "甲乙丙",
            "file-offset",
            "kb-offset",
            "version-offset",
            _params(),
            parsed_segments=[{"start_offset": 0, "end_offset": 1, "text": "乙"}],
            tokenizer=CharacterTokenizer(),
        )


def test_invalid_page_spans_fail_closed() -> None:
    """重叠页码区间和非法页码必须被拒绝。"""
    with pytest.raises(ValueError, match="页码 span"):
        chunk_parent_child(
            "甲乙丙",
            "file-page-invalid",
            "kb-page-invalid",
            "version-page-invalid",
            _params(),
            page_spans=[(0, 2, 1), (1, 3, 2)],
            tokenizer=CharacterTokenizer(),
        )


def test_tokenizer_unavailable_does_not_fallback_to_approximate_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider 和 tiktoken 同时不可用时必须抛出明确错误。"""

    def blocked_import(name: str):
        """阻断 tiktoken 模块解析以覆盖不可用路径。"""
        if name == "tiktoken":
            raise ImportError("blocked for test")
        return importlib.import_module(name)

    monkeypatch.setattr(importlib, "import_module", blocked_import)
    with pytest.raises(TokenizerUnavailableError, match="tiktoken 不可用"):
        resolve_tokenizer()


def test_tiktoken_fallback_respects_token_limits() -> None:
    """真实 tiktoken 的 BPE 前缀漂移也不能使父子块超出配置上限。"""
    result = chunk_parent_child(
        "甲乙丙\n丁戊己" * 400,
        "file-tiktoken",
        "kb-tiktoken",
        "version-tiktoken",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=0),
    )

    assert result["parents"]
    assert max(parent["token_count"] for parent in result["parents"]) <= 256
    assert max(child["token_count"] for child in result["children"]) <= 64


def test_tiktoken_no_overlap_keeps_child_intervals_contiguous() -> None:
    """无重叠窗口必须覆盖父块原文，不因 BPE 前缀漂移丢失字符。"""
    result = chunk_parent_child(
        "甲乙丙\n丁戊己" * 400,
        "file-contiguous",
        "kb-contiguous",
        "version-contiguous",
        _params(parent_token_num=256, child_token_num=64, child_overlap_percent=0),
    )
    children_by_parent: dict[str, list[dict]] = {}
    for child in result["children"]:
        children_by_parent.setdefault(child["parent_id"], []).append(child)
    for children in children_by_parent.values():
        for previous, current in zip(children, children[1:]):
            assert previous["end_offset"] == current["start_offset"]


def test_invalid_provider_tokenizer_is_rejected() -> None:
    """provider tokenizer 缺少 encode 接口时不得悄悄切换本地 tokenizer。"""
    with pytest.raises(TokenizerUnavailableError, match="provider tokenizer"):
        resolve_tokenizer(object(), tokenizer_id="provider:test")
