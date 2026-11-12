"""真实兼容模型 HTTP 错误触发压缩与摘要输入缩减。"""

import json
from types import SimpleNamespace

import httpx
import pytest
from deepagents.backends.filesystem import FilesystemBackend
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from test.integration.support.module_audit import serve_http
from yuxi.modules.agents.runtime.middlewares.summary import YuxiSummarizationMiddleware

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_server_overflow_triggers_compaction_and_shrinks_summary_requests(tmp_path):
    """实际 provider 的超长错误触发压缩，摘要失败后按比例重试且原文可回读。"""
    app = FastAPI()
    received, summary_prompts = [], []

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        """提供兼容模型协议，明确拒绝首次主请求及前两次摘要请求。"""
        body = await request.json()
        received.append(body)
        first_content = body["messages"][0]["content"]
        is_summary = isinstance(first_content, str) and first_content.startswith("Summarize:")
        if is_summary:
            summary_prompts.append(first_content)
        if len(received) == 1 or is_summary and len(summary_prompts) < 3:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "context_length_exceeded", "type": "invalid_request_error", "message": "prompt is too long"}},
            )
        return {
            "id": "isolated-completion",
            "object": "chat.completion",
            "created": 1,
            "model": "isolated-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Recovered summary" if is_summary else "Final response"},
                    "finish_reason": "stop",
                }
            ],
        }

    messages = [
        HumanMessage(content="ORIGINAL_START " + "history " * 10000 + " ORIGINAL_END"),
        AIMessage(content="old answer"),
        HumanMessage(content="continue"),
    ]
    original = [message.model_dump() for message in messages]
    async with serve_http(app) as client, httpx.AsyncClient(trust_env=False) as model_client:
        model = ChatOpenAI(
            model="isolated-model",
            api_key="synthetic-test-key",
            base_url=f"{str(client.base_url).rstrip('/')}/v1",
            max_retries=0,
            http_async_client=model_client,
            profile={"max_input_tokens": 8192, "max_output_tokens": 128},
        )
        middleware = YuxiSummarizationMiddleware(
            model=model,
            backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
            trigger=("tokens", 1_000_000),
            keep=("messages", 1),
            summary_prompt="Summarize: {messages}",
        )

        async def handler(request):
            """实际主模型调用也经过同一 provider 适配器。"""
            return ModelResponse(result=[await model.ainvoke(request.messages)])

        request = ModelRequest(model=model, messages=messages, tools=[], state={"messages": messages}, runtime=SimpleNamespace(context={}))
        result = await middleware.awrap_model_call(request, handler)

    assert isinstance(result, ExtendedModelResponse)
    assert result.model_response.result[0].content == "Final response"
    assert len(received) == 5 and len(summary_prompts) == 3
    sizes = [len(prompt.removeprefix("Summarize: ").encode()) for prompt in summary_prompts]
    assert all(current <= previous * 4 // 5 for previous, current in zip(sizes, sizes[1:]))
    assert [message.model_dump() for message in messages] == original
    event = result.command.update["_summarization_event"]
    archive = (tmp_path / event["file_path"].lstrip("/")).read_text()
    assert "ORIGINAL_START" in archive and "ORIGINAL_END" in archive
    assert "Recovered summary" in json.dumps(received[-1], ensure_ascii=False)
