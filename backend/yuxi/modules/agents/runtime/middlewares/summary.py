"""Yuxi 对 DeepAgents 会话摘要中间件的适配。"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from deepagents.middleware.summarization import (
    Command,
    ContextOverflowError,
    SummarizationMiddleware,
    _aclip_overflow_tail,
)
from langchain.agents.middleware.summarization import ContextSize
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage, get_buffer_string
from langchain_core.messages.utils import count_tokens_approximately
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.config import get_stream_writer
from langgraph.constants import TAG_NOSTREAM

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.agents.runtime.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_YUXI_SUMMARY_PROMPT,
)
from yuxi.modules.models.chat import load_chat_model, resolve_chat_model_spec

_APPROX_CHARS_PER_TOKEN = 4
_DEFAULT_TOOL_ARG_MAX_LENGTH = 2000
_TRUNCATED_TOOL_ARG_TEXT = "...(argument truncated for context view)"
_TOOL_RESULT_SAVED_MARKER = "yuxi_tool_result_saved"


class YuxiSummarizationMiddleware(SummarizationMiddleware):
    """先确定性压缩工具结果，再按同一压力阈值决定是否生成摘要。"""

    _SUMMARY_INVOKE_CONFIG = {"metadata": {"lc_source": "summarization"}, "tags": [TAG_NOSTREAM]}

    def __init__(
        self,
        *args,
        trigger: tuple[str, int],
        tool_result_offload_token_limit: int | None = DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        tool_arg_max_length: int = _DEFAULT_TOOL_ARG_MAX_LENGTH,
        authorization_context=None,
        **kwargs,
    ) -> None:
        if trigger[0] != "tokens":
            raise ValueError("摘要触发条件必须使用 token 数量")
        super().__init__(*args, trigger=trigger, **kwargs)
        self._trigger_tokens = trigger[1]
        self.tool_result_offload_token_limit = tool_result_offload_token_limit
        self.tool_arg_max_length = tool_arg_max_length
        self.authorization_context = authorization_context

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步入口不能执行依赖异步授权池的摘要流程。"""
        raise RuntimeError("Yuxi 摘要仅支持异步调用，请使用 awrap_model_call")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """自动压缩入口拥有一次完整的事件生命周期。"""
        effective_messages = self._get_effective_messages(request)
        total_tokens = self._count_tokens(effective_messages, request.system_message, request.tools)
        overflow_triggered = False
        if total_tokens < self._trigger_tokens:
            try:
                return await handler(request.override(messages=effective_messages))
            except ContextOverflowError:
                overflow_triggered = True

        _emit_compression("started")
        try:
            result, event = await self._awrap_model_call_with_compaction(request, handler, effective_messages, overflow_triggered)
        except (Exception, asyncio.CancelledError) as exc:
            _emit_compression("failed", error=repr(exc))
            raise
        _emit_compression("completed", **event)
        return result

    async def aforce_summarize(self, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """主动压缩已有 checkpoint，并返回待持久化更新与结果指标。"""
        messages = list(state.get("messages") or [])
        previous_event = state.get("_summarization_event")
        effective_messages = self._apply_event_to_messages(messages, previous_event)
        before_tokens = self._count_tokens(effective_messages, None, [])
        cutoff_index = self._determine_cutoff_index(effective_messages)
        if cutoff_index <= 0:
            return {}, {
                "status": "no_op",
                "before_tokens": before_tokens,
                "after_tokens": before_tokens,
                "reason": "insufficient_history",
            }

        compacted_messages = await self._compact_messages(effective_messages)
        _, _, event, session_id = await self._summarize_history(state, effective_messages, compacted_messages, cutoff_index)
        file_path = event["file_path"]
        update = {"_summarization_event": event, "_summarization_session_id": session_id}
        persisted_messages = self._apply_event_to_messages(messages, update["_summarization_event"])
        after_tokens = self._count_tokens(persisted_messages, None, [])
        return update, {
            "status": "completed",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "compressed_messages": cutoff_index,
            "file_path": file_path,
        }

    async def _awrap_model_call_with_compaction(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        effective_messages: list[AnyMessage],
        overflow_triggered: bool,
    ) -> tuple[ModelResponse | ExtendedModelResponse, dict[str, Any]]:
        compacted_messages = await self._compact_messages(effective_messages)
        compacted_tokens = self._count_tokens(compacted_messages, request.system_message, request.tools)
        should_summarize = overflow_triggered or compacted_tokens >= self._trigger_tokens
        if not should_summarize:
            try:
                response = await handler(request.override(messages=compacted_messages))
            except ContextOverflowError:
                overflow_triggered = True
            else:
                return response, {}

        cutoff_index = self._determine_cutoff_index(compacted_messages)
        if cutoff_index <= 0:
            response = await handler(request.override(messages=compacted_messages))
            return response, {}

        new_messages, preserved_messages, new_event, session_id = await self._summarize_history(
            request.state, effective_messages, compacted_messages, cutoff_index
        )
        if overflow_triggered:
            preserved_messages, _ = await _aclip_overflow_tail(
                preserved_messages,
                self._backend,
                keep=self._lc_helper.keep,
                max_input_tokens=self._get_profile_limits(),
                token_counter=self.token_counter,
                large_tool_results_prefix=self._large_tool_results_prefix,
            )
        file_path = new_event["file_path"]
        response = await handler(request.override(messages=[*new_messages, *preserved_messages]))
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"_summarization_event": new_event, "_summarization_session_id": session_id}),
        ), {"cutoff_index": new_event["cutoff_index"], "file_path": file_path}

    async def _summarize_history(self, state, effective_messages, compacted_messages, cutoff_index):
        """选择历史、保存原文并构造对应原始位置的摘要事件。"""
        selected, preserved = self._partition_messages(compacted_messages, cutoff_index)
        original, _ = self._partition_messages(effective_messages, cutoff_index)
        archived, failed_media = await self._aoffload_inline_media(self._backend, original)
        if failed_media:
            raise RuntimeError("对话媒体归档失败")
        session_id = self._get_session_id(state)
        file_path = await self._aoffload_to_backend(self._backend, archived, session_id)
        if file_path is None:
            raise RuntimeError("无法保存可恢复的对话历史")
        summary = await self._acreate_summary(selected)
        new_messages = self._build_new_messages_with_path(summary, file_path)
        event = self._build_summary_event(state, cutoff_index, new_messages[0], file_path)
        return new_messages, preserved, event, session_id

    async def _compact_messages(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """压缩过大的工具结果及文件写入参数，保持消息顺序和标识不变。"""
        compacted: list[AnyMessage] = []
        modified = False
        for message in messages:
            updated = message
            if isinstance(message, AIMessage):
                updated = _truncate_ai_tool_call_args(message, max_length=self.tool_arg_max_length)
            elif (
                isinstance(message, ToolMessage)
                and getattr(message, "additional_kwargs", {}).get(_TOOL_RESULT_SAVED_MARKER) is not True
                and _should_offload_tool_message(message, self.tool_result_offload_token_limit)
            ):
                updated = await _replace_tool_message_content(
                    message,
                    backend=self._backend,
                    tool_result_token_limit=self.tool_result_offload_token_limit,
                    large_tool_results_prefix=self._large_tool_results_prefix,
                )
            compacted.append(updated)
            modified = modified or updated is not message
        return compacted if modified else messages

    def _build_summary_prompt(self, messages: list[AnyMessage], *, history_budget: int | None = None) -> tuple[str | None, int, int]:
        """按已知模型窗口和输出预留预算裁剪序列化历史。"""
        window = self._get_profile_limits()
        profile = getattr(self.model, "profile", None) or {}
        configured_output = getattr(self.model, "max_tokens", None) or getattr(self.model, "max_output_tokens", None)
        configured_output = configured_output or profile.get("max_output_tokens")
        if window:
            output = configured_output or max(window // 4, 1)
            if not isinstance(output, int) or isinstance(output, bool) or output <= 0 or output >= window:
                raise RuntimeError("摘要模型输出预算超过上下文窗口")
            budget = (window - output) * 9 // 10
        else:
            # 未知窗口不能证明适配；独立小预算保留兼容，并记录能力限制。
            output = min(configured_output, 1024) if isinstance(configured_output, int) and configured_output > 0 else 1024
            budget = 2048
            logger.bind(component="summary", window_known=False).warning("摘要模型上下文窗口未知，使用保守输入与输出上限")
        template = self._lc_helper.summary_prompt

        def build(history: str) -> str:
            return template.format(messages=history).rstrip()

        message_overhead = count_tokens_approximately([HumanMessage(content="")], use_usage_metadata_scaling=False)

        def count(prompt: str) -> int:
            # UTF-8 字节数对常见字节级分词器比字符近似更保守，仍不是计费值。
            return len(prompt.encode("utf-8")) + message_overhead

        fixed_size = count(build(""))
        if fixed_size >= budget:
            raise RuntimeError("摘要指令超过模型输入预算")
        if history_budget is not None:
            budget = min(budget, fixed_size + history_budget)
        history = get_buffer_string(messages, format="xml")
        prompt = build(history)
        if count(prompt) <= budget:
            return prompt, output, count(prompt) - fixed_size
        low, high = 1, len(history)
        best = ""
        while low <= high:
            length = (low + high) // 2
            head = length // 2
            tail = length - head
            excerpt = (
                "<history_excerpt><start>"
                + html.escape(history[:head], quote=False)
                + "</start><end>"
                + html.escape(history[-tail:], quote=False)
                + "</end></history_excerpt>"
            )
            candidate = build(excerpt)
            if count(candidate) <= budget:
                best = candidate
                low = length + 1
            else:
                high = length - 1
        if not best:
            return None, output, 0
        return best, output, count(best) - fixed_size

    async def _acreate_summary(self, messages: list[AnyMessage]) -> str:
        """执行实际摘要请求。"""
        if not messages:
            raise RuntimeError("没有可供压缩的对话历史")
        prompt, output, history_size = self._build_summary_prompt(messages)
        if prompt is None:
            raise RuntimeError("摘要输入预算不足以容纳历史")
        for attempt in range(6):
            await self._authorize_summary_request()
            output_arg = {"max_output_tokens": output} if isinstance(self.model, ChatGoogleGenerativeAI) else {"max_tokens": output}
            try:
                response = await self.model.ainvoke(prompt, config=self._SUMMARY_INVOKE_CONFIG, **output_arg)
            except ContextOverflowError:
                if attempt == 5 or history_size <= 1:
                    raise
                # 只缩小实际发送的历史预算，固定指令与输出预留保持不变。
                history_budget = history_size * 4 // 5
                next_prompt, output, history_size = self._build_summary_prompt(messages, history_budget=history_budget)
                if next_prompt is None:
                    logger.bind(component="summary", retry=attempt + 1).warning("摘要历史已无法继续缩减，保留服务端上下文超长错误")
                    raise
                prompt = next_prompt
                logger.bind(component="summary", retry=attempt + 1, history_budget=history_budget).warning(
                    "摘要请求超过服务端上下文窗口，缩小历史输入后重试"
                )
            else:
                break
        summary = response.text.strip()
        if not summary:
            raise RuntimeError("摘要模型返回空内容")
        return summary

    async def _authorize_summary_request(self) -> None:
        """摘要模型同样使用实际调用者的当前账号和 Agent 授权。"""
        if self.authorization_context is not None:
            from yuxi.modules.agents.runtime.middlewares.authorization import refresh_execution_authorization

            await refresh_execution_authorization(self.authorization_context)

    def _build_summary_event(
        self,
        state: dict[str, Any],
        cutoff_index: int,
        summary_message: AnyMessage,
        file_path: str | None,
    ) -> dict[str, Any]:
        return {
            "cutoff_index": self._compute_state_cutoff(state.get("_summarization_event"), cutoff_index),
            "summary_message": summary_message,
            "file_path": file_path,
        }


def create_summary_middleware(
    model: str | BaseChatModel,
    *,
    backend,
    trigger: tuple[str, int],
    keep: ContextSize | list[ContextSize] | None,
    summary_prompt: str | None = None,
    authorization_context=None,
    tool_result_offload_token_limit: int | None = DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
) -> YuxiSummarizationMiddleware:
    """创建绑定单次运行 backend 的摘要中间件；tool_result_offload_token_limit 为 None 时关闭工具结果 offload。"""
    middleware_kwargs = {
        "model": model,
        "backend": backend,
        "trigger": trigger,
        "keep": keep,
        "token_counter": _count_tokens_for_summary_trigger,
        "tool_result_offload_token_limit": tool_result_offload_token_limit,
        "authorization_context": authorization_context,
    }
    if summary_prompt and summary_prompt.strip():
        middleware_kwargs["summary_prompt"] = summary_prompt
    return YuxiSummarizationMiddleware(**middleware_kwargs)


def create_summary_middleware_from_context(context, *, backend, model=None) -> YuxiSummarizationMiddleware:
    """按 Agent 运行时配置创建自动与主动压缩共用的摘要器。"""
    trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    if model is None:
        model_spec = resolve_chat_model_spec(context.model)
        model = load_chat_model(fully_specified_name=model_spec, session_id=context.thread_id, uid=context.uid, max_retries=0)
    return create_summary_middleware(
        model=model,
        backend=backend,
        authorization_context=context,
        trigger=("tokens", trigger_tokens),
        keep=("messages", getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES)),
        summary_prompt=getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT,
        tool_result_offload_token_limit=getattr(
            context,
            "summary_tool_result_token_limit",
            DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
        ),
    )


def _emit_compression(status: str, **extra: Any) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"type": "yuxi.context_compression", "status": status, **extra})


def _count_tokens_for_summary_trigger(messages: Iterable[Any], **kwargs: Any) -> int:
    kwargs.pop("use_usage_metadata_scaling", None)
    return count_tokens_approximately(messages, use_usage_metadata_scaling=False, **kwargs)


def _build_tool_result_preview(
    content: str,
    token_limit: int | None,
) -> tuple[str, int]:
    """在单条工具预算内生成通用首中尾预览。"""
    text = content.strip()
    if token_limit is None:
        return text, 0
    if token_limit <= 0:
        return "", len(text)

    max_chars = token_limit * _APPROX_CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text, 0

    preview = _generic_tool_result_preview(text, max_chars)
    return preview, len(text) - len(preview)


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item if isinstance(item, str) else item["text"]
            for item in content
            if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _tool_result_path(tool_name: str | None, digest: str, prefix: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (tool_name or "").strip()).strip(".-") or "tool-result"
    return f"{prefix}/{safe_name}-{digest[:16]}.txt"


async def _write_tool_result(backend, path: str, content: str) -> str:
    if backend is None:
        raise RuntimeError(f"Cannot save tool result to {path}: backend is unavailable")
    result = await backend.awrite(path, content)
    error = getattr(result, "error", None)
    if not error:
        return path
    if "already exists" in str(error).lower():
        files = await backend.adownload_files([path])
        if len(files) == 1 and files[0].error is None and files[0].content == content.encode("utf-8"):
            return path
    raise RuntimeError(f"Failed to write tool result to {path}: {error}")


def _should_offload_tool_message(message: ToolMessage, token_limit: int | None) -> bool:
    """token_limit 为 None 表示关闭工具结果 offload；非正值表示全部 offload。"""
    if token_limit is None:
        return False
    if token_limit <= 0:
        return True
    content = _extract_text_content(message.content)
    estimated_tokens = max((len(content) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN, 1)
    return estimated_tokens > token_limit


async def _replace_tool_message_content(
    message: ToolMessage,
    *,
    backend,
    tool_result_token_limit: int | None,
    large_tool_results_prefix: str,
) -> ToolMessage:
    content = _extract_text_content(message.content)
    tool_name = message.name if isinstance(message.name, str) and message.name else None
    original = json.dumps(message.content, ensure_ascii=False) if isinstance(message.content, list) else content
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    path = await _write_tool_result(backend, _tool_result_path(tool_name, digest, large_tool_results_prefix), original)
    preview, omitted_chars = _build_tool_result_preview(
        content,
        tool_result_token_limit,
    )
    approx_tokens = max((len(content) + _APPROX_CHARS_PER_TOKEN - 1) // _APPROX_CHARS_PER_TOKEN, 1)
    lines = [
        "[Tool result saved]",
        f"Tool: {tool_name or 'unknown'}",
        f"Approx tokens: {approx_tokens}",
        f"SHA-256: {digest}",
        f"Full output path: {path}",
    ]
    if preview:
        lines.extend(["", "Output preview:", preview])
    if omitted_chars:
        lines.append(f"[Truncated {omitted_chars} chars. Read the full output from the saved file.]")

    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs[_TOOL_RESULT_SAVED_MARKER] = True
    preview_content = "\n".join(lines)
    if isinstance(message.content, list):
        non_text = [block for block in message.content if isinstance(block, dict) and block.get("type") not in {"text", "output_text"}]
        preview_content = [{"type": "text", "text": preview_content}, *non_text]
    return message.model_copy(update={"content": preview_content, "additional_kwargs": additional_kwargs})


def _generic_tool_result_preview(text: str, max_chars: int) -> str:
    labels = ("[HEAD]\n", "\n\n[MIDDLE]\n", "\n\n[TAIL]\n")
    content_budget = max_chars - sum(len(label) for label in labels)
    if content_budget <= 0:
        return text[:max_chars]

    head_length = (content_budget * 2) // 5
    middle_length = content_budget // 5
    tail_length = content_budget - head_length - middle_length
    middle_start = max((len(text) - middle_length) // 2, head_length)
    return "".join(
        (
            labels[0],
            text[:head_length],
            labels[1],
            text[middle_start : middle_start + middle_length],
            labels[2],
            text[-tail_length:],
        )
    )


def _truncate_ai_tool_call_args(message: AIMessage, *, max_length: int) -> AIMessage:
    if not message.tool_calls and not getattr(message, "additional_kwargs", None):
        return message

    updated_tool_calls = []
    tool_calls_modified = False
    for tool_call in message.tool_calls or []:
        if not isinstance(tool_call, dict):
            updated_tool_calls.append(tool_call)
            continue
        updated, modified = _truncate_tool_call_args(tool_call, max_length)
        updated_tool_calls.append(updated)
        tool_calls_modified = tool_calls_modified or modified

    additional_kwargs, provider_calls_modified = _truncate_provider_tool_calls(
        dict(getattr(message, "additional_kwargs", {}) or {}),
        max_length,
        updated_tool_calls,
    )
    if not tool_calls_modified and not provider_calls_modified:
        return message

    updated_message = message.model_copy(update={"additional_kwargs": additional_kwargs})
    if tool_calls_modified:
        updated_message.tool_calls = updated_tool_calls
    return updated_message


def _truncate_tool_call_args(tool_call: dict[str, Any], max_length: int) -> tuple[dict[str, Any], bool]:
    args = tool_call.get("args")
    if tool_call.get("name") not in {"write_file", "edit_file"} or not isinstance(args, dict):
        return tool_call, False

    text_keys = {"content"} if tool_call["name"] == "write_file" else {"old_string", "new_string"}
    truncated_args = {
        key: _truncate_string_arg(value, max_length) if key in text_keys and isinstance(value, str) else value
        for key, value in args.items()
    }
    if truncated_args == args:
        return tool_call, False
    return {**tool_call, "args": truncated_args}, True


def _truncate_provider_tool_calls(
    additional_kwargs: dict[str, Any],
    max_length: int,
    normalized_calls: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return additional_kwargs, False

    by_id = {call.get("id"): call for call in normalized_calls if call.get("id")}
    updated_tool_calls = []
    modified = False
    for raw_call in raw_tool_calls:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if not isinstance(arguments, str) or function.get("name") not in {"write_file", "edit_file"}:
            updated_tool_calls.append(raw_call)
            continue
        try:
            raw_args = json.loads(arguments)
        except (ValueError, TypeError):
            updated_tool_calls.append(raw_call)
            continue
        normalized = by_id.get(raw_call.get("id"))
        if normalized is not None and normalized.get("name") == function["name"]:
            args = normalized["args"]
        else:
            call, _ = _truncate_tool_call_args({"name": function["name"], "args": raw_args}, max_length)
            args = call["args"]
        if args == raw_args:
            updated_tool_calls.append(raw_call)
            continue
        updated_tool_calls.append({**raw_call, "function": {**function, "arguments": json.dumps(args, ensure_ascii=False)}})
        modified = True

    return ({**additional_kwargs, "tool_calls": updated_tool_calls}, True) if modified else (additional_kwargs, False)


def _truncate_string_arg(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:20]}{_TRUNCATED_TOOL_ARG_TEXT}"
