"""验证翻译服务的输入约束、执行预算与错误边界。"""

import asyncio
from typing import TypedDict, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.translation import services
from yuxi.shared.errors import InvalidInputError, OperationTimeoutError, ServiceUnavailableError, UpstreamError


class TranslationInput(TypedDict):
    """真实翻译用例所需的默认测试参数。"""

    uid: str
    model_spec: str
    text: str
    source_language: str
    target_language: str


DEFAULT_INPUT: TranslationInput = {
    "uid": "translation-user",
    "model_spec": "fixture:chat",
    "text": "A short sentence.",
    "source_language": "en",
    "target_language": "zh",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"text": " \n "}, "translation_text_required"),
        ({"text": "文" * 10_001}, "translation_text_too_long"),
        ({"source_language": "unknown"}, "translation_language_unsupported"),
        ({"target_language": "auto"}, "translation_language_unsupported"),
        ({"target_language": "en"}, "translation_language_same"),
        ({"model_spec": "fixture:"}, "translation_model_spec_invalid"),
    ],
)
async def test_translate_rejects_invalid_input(overrides: dict[str, str], code: str) -> None:
    """无效文本、语言及模型不能进入配置查询或外部调用。"""
    db = MagicMock(spec=AsyncSession)
    db.execute.side_effect = AssertionError("无效输入不应查询模型")
    with pytest.raises(InvalidInputError) as error:
        await services.translate_text(db, **cast(TranslationInput, DEFAULT_INPUT | overrides))
    assert error.value.code == code


@pytest.mark.asyncio
async def test_translation_cancels_slow_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时向用户表达重试并中断在途模型等待。"""
    cancelled = asyncio.Event()

    async def slow_reply(_messages: list[tuple[str, str]]) -> AIMessage:
        """模拟模型尚未返回时由服务超时取消。"""
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()
        return AIMessage(content="迟到结果")

    monkeypatch.setattr(services, "_require_enabled_chat_model", AsyncMock())
    monkeypatch.setattr(services, "load_chat_model", lambda *args, **kwargs: MagicMock(ainvoke=slow_reply))
    monkeypatch.setattr(services, "TRANSLATION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(OperationTimeoutError) as error:
        await services.translate_text(MagicMock(spec=AsyncSession), **DEFAULT_INPUT)
    assert error.value.code == "translation_timeout"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_translation_rejects_malformed_provider_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 拒绝端点时返回模型不可用，隐藏原始供应商地址。"""
    monkeypatch.setattr(services, "_require_enabled_chat_model", AsyncMock())
    monkeypatch.setattr(services, "load_chat_model", MagicMock(side_effect=httpx.InvalidURL("private-provider-endpoint")))
    with pytest.raises(ServiceUnavailableError) as error:
        await services.translate_text(MagicMock(spec=AsyncSession), **DEFAULT_INPUT)
    assert error.value.code == "translation_model_unavailable"
    assert "private-provider-endpoint" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "failure", "code"),
    [
        (AIMessage(content="\n "), None, "translation_empty_result"),
        (None, RuntimeError("fixture-private-upstream-detail"), "translation_upstream_failed"),
    ],
)
async def test_translation_rejects_failed_or_empty_results(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | None,
    failure: Exception | None,
    code: str,
) -> None:
    """空响应和供应商异常保留公开结局，隐藏内部细节。"""
    monkeypatch.setattr(services, "_require_enabled_chat_model", AsyncMock())
    reply = AsyncMock(return_value=response, side_effect=failure)
    monkeypatch.setattr(services, "load_chat_model", lambda *args, **kwargs: MagicMock(ainvoke=reply))
    with pytest.raises(UpstreamError) as error:
        await services.translate_text(MagicMock(spec=AsyncSession), **DEFAULT_INPUT)
    assert error.value.code == code
    assert "fixture-private-upstream-detail" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "upstream", "expected"),
    [
        ("Good morning.", "<think>先判断语种</think>早上好。", "早上好。"),
        ("Good morning.", "早上好。</think>早上好。", "早上好。"),
        ("Say <think>literally.", "按字面读 <think>。", "按字面读 <think>。"),
    ],
)
async def test_translation_projects_visible_text(monkeypatch: pytest.MonkeyPatch, source: str, upstream: str, expected: str) -> None:
    """只展示译文，同时保留用户原文中的字面标记。"""
    monkeypatch.setattr(services, "_require_enabled_chat_model", AsyncMock())
    model = MagicMock(ainvoke=AsyncMock(return_value=AIMessage(content=upstream)))
    monkeypatch.setattr(services, "load_chat_model", MagicMock(return_value=model))
    payload = DEFAULT_INPUT.copy()
    payload["text"] = source
    result = await services.translate_text(MagicMock(spec=AsyncSession), **payload)
    assert result == expected
