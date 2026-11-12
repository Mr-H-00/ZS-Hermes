"""实际记录器与公开适配器共同消费工具结果，以 PG 行和协议结果作 oracle。"""

from datetime import UTC, datetime

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select

from test.integration.services.agent_input_fixtures import start_input_run
from test.integration.services.agent_input_fixtures import sessions as sessions
from yuxi.modules.agents.models.messages import TOOL_AUDIT_MESSAGE_TYPE, Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.services.message_recorder import RunMessageRecorder
from yuxi.modules.agents.services.message_audits import serialize_tool_audit
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    ("content", "message", "expected_output", "expected_text", "expected_error"),
    [
        ({"reason": "bad"}, None, '{"reason": "bad"}', '{"reason": "bad"}', '{"reason": "bad"}'),
        (
            [{"type": "text", "text": "错误详情"}],
            None,
            [{"type": "input_text", "text": "错误详情"}],
            '[{"type": "text", "text": "错误详情"}]',
            '[{"type": "text", "text": "错误详情"}]',
        ),
        ("", None, "", "", "工具执行失败"),
        ("", "明确错误", "", "", "明确错误"),
    ],
)
@pytest.mark.parametrize("started", [True, False])
async def test_failed_tool_audit_and_public_output_use_same_normalized_result(
    sessions, content, message, expected_output, expected_text, expected_error, started
):
    """原始结果只保存一次，公开内容与审计文本从同一 Run 结果派生。"""
    run = await start_input_run(sessions)
    recorder = RunMessageRecorder(run_id=run.id, thread_id=run.thread_id, worker_id="owner")
    adapter = OpenAIEventAdapter(run_id=run.id, turn_id=run.turn_id, thread_id=run.thread_id, worker_id="owner")
    source = {"status": "error", "content": content}
    events = [
        ("messages", {"event": "message-start", "id": "model"}),
        (
            "messages",
            {"event": "content-block-finish", "index": 0, "content": {"type": "tool_call", "id": "call", "name": "test", "args": {}}},
        ),
        ("messages", {"event": "message-finish"}),
    ]
    if started:
        events.append(("tools", {"event": "tool-started", "tool_call_id": "call", "tool_name": "test", "input": {}}))
    events.append(
        ("tools", {"event": "tool-finished" if started else "tool-result", "tool_call_id": "call", "output": source, "message": message})
    )
    published = []
    timestamp = datetime.now(UTC).timestamp() * 1000
    for seq, (method, data) in enumerate(events):
        event = {
            "seq": seq,
            "method": method,
            "params": {
                "timestamp": timestamp + seq,
                "namespace": [],
                "data": (data, {"run_id": "model"}) if method == "messages" else data,
            },
        }
        published.extend(await adapter.consume(await recorder.consume(event)))
    output = next(
        event["item"]
        for event in published
        if event.get("item", {}).get("type") == "function_call_output" and event["item"]["status"] == "failed"
    )
    assert output["output"] == expected_output
    assert output["error"] == expected_error
    async with sessions() as oracle:
        audit = await oracle.scalar(
            select(Message).where(Message.run_id == run.id, Message.operation_id == "call", Message.message_type == TOOL_AUDIT_MESSAGE_TYPE)
        )
        assert audit.turn_id == run.turn_id and audit.execution_status == "failed"
        assert (audit.started_at is not None) is started
        assert (audit.duration_ms is not None) is started
        assert audit.content == ""
        assert serialize_tool_audit(audit)["content"] == expected_text
        assert audit.extra_metadata["error_message"] == expected_error
        assert audit.extra_metadata["output"] == source
        stored = audit.extra_metadata["public_items"]["output"]
        assert stored["output"] == expected_output and stored["error"] == expected_error
    assert source == {"status": "error", "content": content}


@pytest.mark.parametrize("existing_execution", [False, True])
async def test_unstarted_tool_result_rejects_missing_source_or_existing_execution(sessions, existing_execution):
    """短路结果必须有模型来源，且不能覆盖真实执行。"""
    run = await start_input_run(sessions)
    recorder = RunMessageRecorder(run_id=run.id, thread_id=run.thread_id, worker_id="owner")
    timestamp = datetime.now(UTC).timestamp() * 1000
    if existing_execution:
        for seq, data in enumerate(
            [
                {"event": "message-start", "id": "model"},
                {"event": "content-block-finish", "index": 0, "content": {"type": "tool_call", "id": "call", "name": "test", "args": {}}},
                {"event": "message-finish"},
            ]
        ):
            await recorder.consume(
                {
                    "seq": seq,
                    "method": "messages",
                    "params": {"timestamp": timestamp + seq, "namespace": [], "data": (data, {"run_id": "model"})},
                }
            )
        await recorder.consume(
            {
                "seq": 3,
                "method": "tools",
                "params": {
                    "timestamp": timestamp + 3,
                    "namespace": [],
                    "data": {"event": "tool-started", "tool_call_id": "call", "tool_name": "test", "input": {}},
                },
            }
        )
    with pytest.raises(ValueError, match="覆盖已有工具执行" if existing_execution else "模型声明"):
        await recorder.consume(
            {
                "seq": 4,
                "method": "tools",
                "params": {
                    "timestamp": timestamp + 4,
                    "namespace": [],
                    "data": {"event": "tool-result", "tool_call_id": "call", "output": {"status": "error", "content": "denied"}},
                },
            }
        )
    async with sessions() as oracle:
        audit = await oracle.scalar(select(Message).where(Message.run_id == run.id, Message.role == "tool"))
        if existing_execution:
            assert audit.execution_status == "running" and audit.started_at is not None
            assert audit.finished_at is None and "output" not in audit.extra_metadata
        else:
            assert audit is None


async def test_checkpoint_tool_error_reconcile_saves_public_items(sessions):
    """终态错误对账同时关闭公开调用和结果，保留尚未裁决的调用。"""
    run = await start_input_run(sessions)
    recorder = RunMessageRecorder(run_id=run.id, thread_id=run.thread_id, worker_id="owner")
    adapter = OpenAIEventAdapter(run_id=run.id, turn_id=run.turn_id, thread_id=run.thread_id, worker_id="owner")
    timestamp = datetime.now(UTC).timestamp() * 1000
    seq = 0

    async def project(method, data):
        """经过生产审计与公开投影链路，保留原生顺序。"""
        nonlocal seq
        seq += 1
        event = {
            "seq": seq,
            "method": method,
            "params": {
                "timestamp": timestamp + seq,
                "namespace": [],
                "data": (data, {"run_id": "model"}) if method == "messages" else data,
            },
        }
        return await adapter.consume(await recorder.consume(event))

    await project("messages", {"event": "message-start", "id": "model"})
    for index, call in enumerate(["first", "second", "waiting"]):
        await project(
            "messages",
            {
                "event": "content-block-finish",
                "index": index,
                "content": {"type": "tool_call", "id": call, "name": "test", "args": {}},
            },
        )
    await project("messages", {"event": "message-finish"})
    for call in ["first", "second", "waiting"]:
        await project("tools", {"event": "tool-started", "tool_call_id": call, "tool_name": "test", "input": {}})
        events = await project("tools", {"event": "tool-error", "tool_call_id": call, "message": "raw observation"})
        assert events == []

    from yuxi.modules.agents.services.checkpoint_messages import _reconcile_tool_error_from_state

    async with sessions() as db:
        for call in ["first", "second"]:
            message = ToolMessage(content=f"{call} failure", tool_call_id=call, status="error")
            await _reconcile_tool_error_from_state(
                db,
                run_id=run.id,
                thread_id=run.thread_id,
                worker_id="owner",
                tool_call_id=call,
                msg_dict=message.model_dump(mode="json"),
            )
        await db.commit()
    events = await adapter.finish()
    done = [event for event in events if event["type"] == "agent.session.turn.item.done" and event["item"]["status"] == "failed"]
    assert len(done) == 4
    assert len({event["event_id"] for event in done}) == 4
    assert {(event["item"]["call_id"], event["item"]["status"]) for event in done} == {
        ("first", "failed"),
        ("second", "failed"),
    }
    async with sessions() as oracle:
        assert (await oracle.get(AgentRun, run.id)).status == "running"
        rows = list((await oracle.scalars(select(Message).where(Message.run_id == run.id, Message.role == "tool"))).all())
        assert {row.operation_id: row.execution_status for row in rows} == {
            "first": "failed",
            "second": "failed",
            "waiting": "running",
        }
        for row in rows:
            public = row.extra_metadata["public_items"]["output"]
            assert public["status"] == ("in_progress" if row.operation_id == "waiting" else "failed")
            assert row.duration_ms is not None and row.finished_at is not None
        model = await oracle.scalar(select(Message).where(Message.run_id == run.id, Message.operation_id == "model"))
        assert model.extra_metadata["public_items"]["call:first"]["status"] == "failed"
        assert model.extra_metadata["public_items"]["call:waiting"]["status"] == "in_progress"
