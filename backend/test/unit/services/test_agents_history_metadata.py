"""普通历史仅投影公开 item，不暴露模型审计和配置。"""

from datetime import datetime
from types import SimpleNamespace
from yuxi.modules.agents.services.public_items import serialize_public_items


def test_cooperation_pause_is_distinct_from_cancelled_tool():
    """相同 incomplete 协议状态保留正常协作等待原因，取消不会伪装成等待。"""
    item = {"id": "call", "type": "function_call", "status": "in_progress", "yuxi": {"output_index": 1}}
    message = SimpleNamespace(role="assistant", extra_metadata={"public_items": {"call": item}})
    run = SimpleNamespace(status="interrupted", error_type="cooperation_waiting")
    (waiting,) = serialize_public_items(message, run, None)
    assert waiting["status"] == "incomplete"
    assert waiting["yuxi"]["waiting_kind"] == "cooperation"
    run.status = "cancelled"
    (cancelled,) = serialize_public_items(message, run, None)
    assert cancelled["status"] == "incomplete"
    assert "waiting_kind" not in cancelled["yuxi"]
    assert item["status"] == "in_progress"


def test_published_model_history_hides_internal_audit_metadata():
    """未进入用户输出链路的模型审计没有公开 item。"""
    message = SimpleNamespace(
        role="assistant", extra_metadata={"start_metadata": {"prompt": "private"}, "finish_metadata": {"raw": "private"}}
    )
    assert serialize_public_items(message, None, None) == []


def test_user_history_keeps_input_identity_and_hides_internal_metadata():
    """用户输入保留回执关联，内部 metadata 不随 item 返回。"""
    message = SimpleNamespace(
        id=1,
        role="user",
        run_id=None,
        turn_id=None,
        content="hello",
        delivery_status="dispatched",
        source_input_id="input-1", input_position=0, received_at=datetime(2026, 9, 29),
        created_at=datetime(2026, 9, 30),
        message_type="text",
        extra_metadata={ "source": "web", "private": "secret"},
    )
    (item,) = serialize_public_items(message, None, None)
    assert item["yuxi"]["input_id"] == "input-1"
    assert item["content"] == [{"type": "input_text", "text": "hello"}]
    assert "secret" not in str(item)
