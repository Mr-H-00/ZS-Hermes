"""真实 PostgreSQL 上的 E2E 测试 run 行清理语义测试。"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test.live_api_cleanup import (
    delete_e2e_run_rows,
    delete_test_conversation_resources,
    delete_test_conversation_rows,
    list_test_conversation_resources,
    make_test_conversation_metadata,
    make_test_conversation_title,
    validate_test_runs_terminal,
    validate_test_workdirs_exclusive,
)
from yuxi.storage.postgres.models_business import (
    AgentRun,
    AgentRunRequest,
    Conversation,
    ConversationStats,
    Message,
    MessageFeedback,
    ToolCall,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture()
async def cleanup_database():
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _seed_thread(session_factory, *, thread_prefix: str) -> dict:
    """构造一个带输入消息、run、输出消息、tool_call、feedback 与请求的完整线程。"""
    thread_id = f"{thread_prefix}-{uuid.uuid4()}"
    uid = f"pytest-user-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    request_id = f"cleanup-req-{uuid.uuid4()}"
    workdir_path = f"projects/YUXI_TEST_cleanup-{uuid.uuid4()}"
    async with session_factory() as db:
        conversation = Conversation(
            thread_id=thread_id,
            uid=uid,
            agent_id="main",
            title=make_test_conversation_title(thread_prefix),
            status="active",
            workdir_path=workdir_path,
            extra_metadata=make_test_conversation_metadata(thread_prefix),
        )
        db.add(conversation)
        await db.flush()
        stats = ConversationStats(conversation_id=conversation.id)
        db.add(stats)
        await db.flush()
        input_message = Message(
            conversation_id=conversation.id,
            role="user",
            content="input",
            request_id=request_id,
            delivery_status="dispatched",
        )
        db.add(input_message)
        await db.flush()
        run = AgentRun(
            id=run_id,
            conversation_thread_id=thread_id,
            runtime_scope_id=thread_id,
            agent_slug="main",
            uid=uid,
            request_id=request_id,
            conversation_id=conversation.id,
            input_message_id=input_message.id,
            input_payload={},
            status="completed",
            run_type="chat",
        )
        db.add(run)
        await db.flush()
        output_message = Message(
            conversation_id=conversation.id,
            run_id=run_id,
            request_id=request_id,
            role="assistant",
            content="output",
            delivery_status="complete",
        )
        db.add(output_message)
        await db.flush()
        db.add(ToolCall(message_id=output_message.id, tool_name="fs", tool_input={}))
        db.add(MessageFeedback(message_id=output_message.id, uid=uid, rating="like"))
        db.add(
            AgentRunRequest(
                request_id=request_id,
                uid=uid,
                agent_slug="main",
                conversation_thread_id=thread_id,
                input_message_id=input_message.id,
                input_payload={},
                status="dispatched",
                dispatched_run_id=run_id,
            )
        )
        await db.commit()
        return {
            "thread_id": thread_id,
            "uid": uid,
            "workdir_path": workdir_path,
            "conversation_id": conversation.id,
            "run_id": run_id,
            "input_message_id": input_message.id,
            "output_message_id": output_message.id,
            "request_id": request_id,
            "stats_id": stats.id,
        }


async def _cleanup_seed(session_factory, seeds: list[dict]) -> None:
    async with session_factory() as db:
        conversation_ids = [seed["conversation_id"] for seed in seeds]
        run_ids = [seed["run_id"] for seed in seeds]
        message_ids = [
            message_id for seed in seeds for message_id in (seed["input_message_id"], seed["output_message_id"])
        ]
        await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(message_ids)))
        await db.execute(delete(MessageFeedback).where(MessageFeedback.message_id.in_(message_ids)))
        await db.execute(delete(AgentRunRequest).where(AgentRunRequest.dispatched_run_id.in_(run_ids)))
        await db.execute(delete(Message).where(Message.id.in_(message_ids)))
        await db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await db.execute(delete(ConversationStats).where(ConversationStats.conversation_id.in_(conversation_ids)))
        await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
        await db.commit()


async def test_delete_e2e_run_rows_removes_target_and_preserves_neighbor(cleanup_database):
    """目标线程的 run 及外键依赖全部删除、attempt 级联；相邻线程与无 run 消息保留。"""
    session_factory = cleanup_database
    target = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-target")
    neighbor = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-neighbor")

    try:
        await delete_e2e_run_rows({target["thread_id"]})

        async with session_factory() as db:
            remaining_runs = set(
                (
                    await db.scalars(select(AgentRun.id).where(AgentRun.id.in_([target["run_id"], neighbor["run_id"]])))
                ).all()
            )
            remaining_target_messages = set(
                (
                    await db.scalars(
                        select(Message.id).where(
                            Message.id.in_([target["input_message_id"], target["output_message_id"]])
                        )
                    )
                ).all()
            )
            remaining_requests = await db.scalar(
                select(AgentRunRequest.id).where(AgentRunRequest.dispatched_run_id == target["run_id"])
            )
            remaining_tool_calls = await db.scalar(
                select(ToolCall.id).where(ToolCall.message_id == target["output_message_id"])
            )
            remaining_feedbacks = await db.scalar(
                select(MessageFeedback.id).where(MessageFeedback.message_id == target["output_message_id"])
            )
            neighbor_run = await db.get(AgentRun, neighbor["run_id"])
            neighbor_output = await db.get(Message, neighbor["output_message_id"])
            neighbor_input = await db.get(Message, neighbor["input_message_id"])
            target_conversation = await db.get(Conversation, target["conversation_id"])

        assert remaining_runs == {neighbor["run_id"]}
        assert remaining_target_messages == {target["input_message_id"]}
        assert remaining_requests is None
        assert remaining_tool_calls is None
        assert remaining_feedbacks is None
        assert neighbor_run is not None
        assert neighbor_output is not None
        assert neighbor_input is not None
        # 对话行由应用软删除生命周期管理，清理只删 run 级审计事实。
        assert target_conversation is not None
    finally:
        await _cleanup_seed(session_factory, [target, neighbor])


async def test_delete_e2e_run_rows_is_noop_for_unknown_threads(cleanup_database):
    """不存在的线程 id 不产生任何副作用。"""
    session_factory = cleanup_database
    seed = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-unknown")

    try:
        await delete_e2e_run_rows({"pytest-cleanup-does-not-exist"})

        async with session_factory() as db:
            run = await db.get(AgentRun, seed["run_id"])
            output = await db.get(Message, seed["output_message_id"])
            conversation = await db.get(Conversation, seed["conversation_id"])

        assert run is not None
        assert output is not None
        assert conversation is not None
    finally:
        await _cleanup_seed(session_factory, [seed])


async def test_delete_e2e_run_rows_is_idempotent(cleanup_database):
    """重复执行同一清理不报错（事务内删除，第二次命中 0 行）。"""
    session_factory = cleanup_database
    target = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-idem")

    try:
        await delete_e2e_run_rows({target["thread_id"]})
        await delete_e2e_run_rows({target["thread_id"]})

        async with session_factory() as db:
            remaining = await db.get(AgentRun, target["run_id"])

        assert remaining is None
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_delete_test_conversation_rows_removes_history_and_preserves_neighbor(cleanup_database):
    """物理清理删除目标对话的完整历史，但保留相邻对话。"""
    session_factory = cleanup_database
    target = await _seed_thread(session_factory, thread_prefix="pytest-conv-target")
    neighbor = await _seed_thread(session_factory, thread_prefix="pytest-conv-neighbor")

    try:
        await delete_test_conversation_rows({target["thread_id"]})

        async with session_factory() as db:
            target_conversation = await db.get(Conversation, target["conversation_id"])
            target_stats = await db.get(ConversationStats, target["stats_id"])
            target_run = await db.get(AgentRun, target["run_id"])
            target_input = await db.get(Message, target["input_message_id"])
            target_output = await db.get(Message, target["output_message_id"])
            target_request = await db.scalar(
                select(AgentRunRequest.id).where(AgentRunRequest.request_id == target["request_id"])
            )
            neighbor_conversation = await db.get(Conversation, neighbor["conversation_id"])
            neighbor_stats = await db.get(ConversationStats, neighbor["stats_id"])
            neighbor_run = await db.get(AgentRun, neighbor["run_id"])
            neighbor_output = await db.get(Message, neighbor["output_message_id"])

        assert target_conversation is None
        assert target_stats is None
        assert target_run is None
        assert target_input is None
        assert target_output is None
        assert target_request is None
        assert neighbor_conversation is not None
        assert neighbor_stats is not None
        assert neighbor_run is not None
        assert neighbor_output is not None
    finally:
        await _cleanup_seed(session_factory, [target, neighbor])


async def test_request_prefix_matching_treats_underscores_literally(cleanup_database):
    """统一 request_id 前缀按字面 starts-with 匹配，不把下划线当 SQL 通配符。"""

    session_factory = cleanup_database
    uid = f"pytest-prefix-user-{uuid.uuid4()}"
    valid_thread_id = f"pytest-prefix-valid-{uuid.uuid4()}"
    ordinary_thread_id = f"pytest-prefix-ordinary-{uuid.uuid4()}"
    conversation_ids: list[int] = []
    message_ids: list[int] = []
    request_ids = [f"YUXI_TEST_valid_{uuid.uuid4()}", f"YUXI-TEST-ordinary-{uuid.uuid4()}"]
    try:
        async with session_factory() as db:
            conversations = [
                Conversation(
                    thread_id=valid_thread_id,
                    uid=uid,
                    agent_id="main",
                    title="ordinary valid",
                    workdir_path=f"projects/{valid_thread_id}",
                ),
                Conversation(
                    thread_id=ordinary_thread_id,
                    uid=uid,
                    agent_id="main",
                    title="ordinary neighbor",
                    workdir_path=f"projects/{ordinary_thread_id}",
                ),
            ]
            db.add_all(conversations)
            await db.flush()
            conversation_ids = [conversation.id for conversation in conversations]
            messages = [
                Message(
                    conversation_id=conversation.id,
                    request_id=request_id,
                    role="user",
                    content="input",
                    delivery_status="cancelled",
                )
                for conversation, request_id in zip(conversations, request_ids, strict=True)
            ]
            db.add_all(messages)
            await db.flush()
            message_ids = [message.id for message in messages]
            db.add_all(
                [
                    AgentRunRequest(
                        request_id=request_id,
                        uid=uid,
                        agent_slug="main",
                        conversation_thread_id=conversation.thread_id,
                        input_message_id=message.id,
                        input_payload={},
                        status="cancelled",
                    )
                    for conversation, message, request_id in zip(
                        conversations,
                        messages,
                        request_ids,
                        strict=True,
                    )
                ]
            )
            await db.commit()

        resources = await list_test_conversation_resources(uid)

        assert valid_thread_id in resources
        assert ordinary_thread_id not in resources
    finally:
        async with session_factory() as db:
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.request_id.in_(request_ids)))
            if message_ids:
                await db.execute(delete(Message).where(Message.id.in_(message_ids)))
            if conversation_ids:
                await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            await db.commit()


async def test_workdir_guard_rejects_ancestor_or_descendant_owner(cleanup_database):
    """目标 Project Workdir 与非目标 Conversation 路径嵌套时必须拒绝。"""

    session_factory = cleanup_database
    uid = f"pytest-workdir-user-{uuid.uuid4()}"
    target_thread_id = f"pytest-workdir-target-{uuid.uuid4()}"
    neighbor_thread_id = f"pytest-workdir-neighbor-{uuid.uuid4()}"
    async with session_factory() as db:
        db.add_all(
            [
                Conversation(
                    thread_id=target_thread_id,
                    uid=uid,
                    agent_id="main",
                    title="target",
                    workdir_path="projects/shared",
                ),
                Conversation(
                    thread_id=neighbor_thread_id,
                    uid=uid,
                    agent_id="main",
                    title="neighbor",
                    workdir_path="projects/shared/nested",
                ),
            ]
        )
        await db.commit()
    try:
        with pytest.raises(RuntimeError, match="overlapping Workdir"):
            await validate_test_workdirs_exclusive(
                {(uid, "projects/shared"): {target_thread_id}},
                {target_thread_id},
            )
    finally:
        async with session_factory() as db:
            await db.execute(
                delete(Conversation).where(Conversation.thread_id.in_([target_thread_id, neighbor_thread_id]))
            )
            await db.commit()


async def test_run_guard_rejects_nonterminal_run(cleanup_database):
    """非终态 Run 存在时，清理 guard 必须拒绝后续破坏性动作。"""

    session_factory = cleanup_database
    target = await _seed_thread(session_factory, thread_prefix="pytest-running-guard")
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, target["run_id"])
            assert run is not None
            run.status = "running"
            await db.commit()

        with pytest.raises(RuntimeError, match="not terminal"):
            await validate_test_runs_terminal({target["thread_id"]})
    finally:
        await _cleanup_seed(session_factory, [target])


async def test_resource_cleanup_lock_blocks_concurrent_workdir_owner(cleanup_database, monkeypatch):
    """文件删除期间 Conversation 表锁必须阻止新会话绑定目标 Workdir。"""

    session_factory = cleanup_database
    uid = f"pytest-lock-user-{uuid.uuid4()}"
    target_thread_id = f"pytest-lock-target-{uuid.uuid4()}"
    neighbor_thread_id = f"pytest-lock-neighbor-{uuid.uuid4()}"
    workdir_path = f"projects/YUXI_TEST_lock-{uuid.uuid4()}"
    async with session_factory() as db:
        conversation = Conversation(
            thread_id=target_thread_id,
            uid=uid,
            agent_id="main",
            title=make_test_conversation_title("workdir-lock"),
            status="deleted",
            workdir_path=workdir_path,
            extra_metadata=make_test_conversation_metadata("workdir-lock"),
        )
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id

    insert_blocked: dict[str, bool] = {}
    deletion_committed: dict[str, bool] = {}

    def try_concurrent_insert(_uid: str, _workdir_path: str) -> None:
        """在独立线程和连接中尝试绑定被清理 Workdir。"""

        async def run_insert() -> None:
            conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
            try:
                deletion_committed["value"] = (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM conversations WHERE thread_id = $1",
                        target_thread_id,
                    )
                    == 0
                )
                await conn.execute("SET statement_timeout = '500ms'")
                try:
                    await conn.execute(
                        "INSERT INTO conversations "
                        "(thread_id, uid, agent_id, title, status, is_pinned, workdir_path) "
                        "VALUES ($1, $2, 'main', 'ordinary neighbor', 'active', false, $3)",
                        neighbor_thread_id,
                        uid,
                        workdir_path,
                    )
                except asyncpg.QueryCanceledError:
                    insert_blocked["value"] = True
                else:
                    insert_blocked["value"] = False
            finally:
                await conn.close()

        worker = threading.Thread(target=lambda: asyncio.run(run_insert()))
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert deletion_committed == {"value": True}
        assert insert_blocked == {"value": True}

    monkeypatch.setattr("test.live_api_cleanup.remove_test_workdir", try_concurrent_insert)
    monkeypatch.setattr("test.live_api_cleanup.remove_e2e_thread_storage", lambda _thread_id: None)

    try:
        await delete_test_conversation_resources(
            {(uid, workdir_path): {target_thread_id}},
            {target_thread_id},
        )

        async with session_factory() as db:
            assert await db.get(Conversation, conversation_id) is None
            assert await db.scalar(select(Conversation.id).where(Conversation.thread_id == neighbor_thread_id)) is None
    finally:
        async with session_factory() as db:
            await db.execute(
                delete(Conversation).where(Conversation.thread_id.in_([target_thread_id, neighbor_thread_id]))
            )
            await db.commit()


async def test_resource_cleanup_reports_file_failure_after_database_commit(cleanup_database, monkeypatch):
    """文件清理失败必须显式报告，且不能恢复已提交的 Conversation 行。"""

    session_factory = cleanup_database
    target = await _seed_thread(session_factory, thread_prefix="pytest-cleanup-file-failure")
    workdir_path = f"projects/YUXI_TEST_failure-{uuid.uuid4()}"
    async with session_factory() as db:
        conversation = await db.get(Conversation, target["conversation_id"])
        conversation.status = "deleted"
        conversation.workdir_path = workdir_path
        await db.commit()

    def fail_file_cleanup(_uid: str, _workdir_path: str) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr("test.live_api_cleanup.remove_test_workdir", fail_file_cleanup)
    monkeypatch.setattr("test.live_api_cleanup.remove_e2e_thread_storage", lambda _thread_id: None)

    try:
        with pytest.raises(RuntimeError, match="rows were deleted, but filesystem cleanup failed"):
            await delete_test_conversation_resources(
                {(target["uid"], workdir_path): {target["thread_id"]}},
                {target["thread_id"]},
            )

        async with session_factory() as db:
            assert await db.get(Conversation, target["conversation_id"]) is None
    finally:
        await _cleanup_seed(session_factory, [target])
