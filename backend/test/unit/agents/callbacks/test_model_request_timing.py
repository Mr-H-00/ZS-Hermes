from datetime import datetime

import pytest

from yuxi.agents.callbacks.model_request_timing import FirstModelRequestRecorder

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_recorder_keeps_only_the_first_chat_model_start_timestamp():
    """同一 Run 的回调只保留第一次进入 ChatModel 的时间。"""
    recorder = FirstModelRequestRecorder()

    await recorder.on_chat_model_start({}, [[]], run_id="model-1")
    first_timestamp = recorder.first_model_request_at
    await recorder.on_chat_model_start({}, [[]], run_id="model-2")

    assert isinstance(first_timestamp, datetime)
    assert recorder.first_model_request_at == first_timestamp


async def test_recorder_without_model_start_does_not_persist(monkeypatch):
    """没有真实 ChatModel start 时不制造模型请求时间事实。"""
    recorder = FirstModelRequestRecorder()
    monkeypatch.setattr(recorder, "first_model_request_at", None)
    await recorder.persist(run_id="run-1", worker_id="worker-1")
