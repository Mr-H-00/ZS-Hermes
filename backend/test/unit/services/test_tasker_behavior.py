"""Durable Task 行为单元测试：持久提交、Handler 重建、lease 与终态。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from yuxi.services import task_queue_service, task_service
from yuxi.services.task_registry import get_task_definition
from yuxi.services.task_service import TaskContext, Tasker, process_task
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


@pytest.fixture(autouse=True)
def disable_pending_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认隔离任务完成后的下一批发布，专项测试再覆盖它。"""

    async def no_pending_tasks(*, limit: int = 200) -> list[str]:
        return []

    monkeypatch.setattr(task_service, "publish_pending_tasks", no_pending_tasks)


class FakeRecord(SimpleNamespace):
    def to_dict(self) -> dict[str, Any]:
        data = vars(self).copy()
        for key in ("created_at", "updated_at", "started_at", "completed_at", "heartbeat_at", "lease_expires_at"):
            data[key] = format_utc_datetime(data.get(key))
        return data


def make_record(**overrides) -> FakeRecord:
    now = utc_now_naive()
    data = {
        "id": "task-1",
        "name": "demo",
        "type": "demo",
        "status": "pending",
        "progress": 0.0,
        "message": "等待执行",
        "payload": {},
        "result": None,
        "error": None,
        "cancel_requested": 0,
        "handler_version": 1,
        "dedupe_key": None,
        "attempt_count": 0,
        "worker_id": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "timeout_seconds": 60.0,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    data.update(overrides)
    return FakeRecord(**data)


class FakeRepo:
    """实现 Task Service 单元测试所需的持久边界。"""

    def __init__(self, record: FakeRecord | None = None):
        self.record = record
        self.events: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self.finish_calls: list[dict[str, Any]] = []
        self.claim_allowed = True
        self.live_owner = True
        self.renew_error: Exception | None = None
        self.release_calls: list[str] = []
        self.requeue_calls: list[dict[str, Any]] = []

    async def create(self, task_id: str, data: dict[str, Any]):
        self.events.append("persist")
        if self.record is not None and self.record.dedupe_key == data.get("dedupe_key"):
            return self.record, False
        self.record = make_record(id=task_id, **data)
        return self.record, True

    async def get_by_id(self, task_id: str):
        return self.record if self.record and self.record.id == task_id else None

    async def list(self, status=None, limit=100):
        if self.record is None or (status and self.record.status != status):
            return []
        return [self.record]

    async def claim(self, task_id: str, *, worker_id: str, lease_seconds: float, max_running: int | None = None):
        if not self.claim_allowed or self.record is None or self.record.status != "pending":
            return self.record, False
        now = utc_now_naive()
        self.record.status = "running"
        self.record.worker_id = worker_id
        self.record.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.record.heartbeat_at = now
        self.record.attempt_count += 1
        return self.record, True

    async def check_control(self, task_id: str, *, worker_id: str):
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def renew_lease(self, task_id: str, *, worker_id: str, lease_seconds: float):
        if self.renew_error is not None:
            raise self.renew_error
        return self.live_owner, bool(self.record.cancel_requested if self.record else False)

    async def update_owned(self, task_id: str, *, worker_id: str, data: dict[str, Any]):
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return False
        self.updates.append(data)
        for key, value in data.items():
            setattr(self.record, key, value)
        return True

    async def finish_owned(self, task_id: str, *, worker_id: str, **data):
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return False
        self.finish_calls.append(data)
        self.record.status = data["status"]
        self.record.message = data["message"]
        if "result" in data:
            self.record.result = data["result"]
        self.record.error = data.get("error")
        self.record.worker_id = None
        return True

    async def requeue_owned(self, task_id: str, *, worker_id: str, max_attempts: int, error: str):
        """模拟 owner-fenced 重试入队，供 worker 异常分流测试使用。"""
        self.requeue_calls.append(
            {
                "task_id": task_id,
                "worker_id": worker_id,
                "max_attempts": max_attempts,
                "error": error,
            }
        )
        if not self.live_owner or self.record is None or self.record.worker_id != worker_id:
            return "ownership_lost"
        if self.record.cancel_requested:
            self.record.status = "cancelled"
            self.record.worker_id = None
            return "cancel_requested"
        if self.record.attempt_count >= max_attempts:
            return "exhausted"
        self.record.status = "pending"
        self.record.progress = 0.0
        self.record.message = "任务执行失败，等待重试"
        self.record.result = None
        self.record.error = error
        self.record.worker_id = None
        self.record.heartbeat_at = None
        self.record.lease_expires_at = None
        self.record.completed_at = None
        return "requeued"

    async def release_interrupted_owner(self, task_id: str, *, worker_id: str, error: str, before_fail=None):
        self.release_calls.append(error)
        self.record.status = "failed"
        self.record.error = error
        return self.record.status

    async def request_cancel(self, task_id: str, *, before_cancel=None):
        if self.record is None or self.record.status in {"success", "failed", "cancelled"}:
            return None
        self.record.cancel_requested = 1
        if self.record.status == "pending":
            self.record.status = "cancelled"
        return self.record

    async def delete_terminal(self, task_id: str):
        if self.record and self.record.status in {"success", "failed", "cancelled"}:
            self.record = None
            return True
        return False


@dataclass
class FakeDefinition:
    handler: Any
    version: int = 1
    max_attempts: int = 1
    retryable_exceptions: tuple[type[Exception], ...] = ()

    def load_handler(self):
        return self.handler

    def load_success_handler(self):
        return None

    def load_failure_handler(self):
        return None


def test_parent_child_cleanup_retry_policy_is_bounded() -> None:
    """Parent-Child 清理只登记三次 RuntimeError 重试策略。"""
    definition = get_task_definition("knowledge_parent_child_cleanup")

    assert definition.max_attempts == 3
    assert definition.retryable_exceptions == (RuntimeError,)


async def test_arq_publication_uses_fresh_messages_instead_of_stale_job_lock(monkeypatch):
    calls = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))

    async def pool():
        return Pool()

    monkeypatch.setattr(task_queue_service, "get_arq_pool", pool)

    await task_queue_service.publish_task("task-1")

    assert calls == [(("process_task", "task-1"), {})]


async def test_submit_persists_before_arq_publication(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo

    async def publish(task_id: str):
        assert repo.record is not None
        repo.events.append("publish")
        return True

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", publish)

    task = await tasker.enqueue(name="demo", task_type="demo", payload={"value": 1})

    assert repo.events == ["persist", "publish"]
    assert task.payload == {"value": 1}
    assert task.status == "pending"


async def test_publication_failure_keeps_persisted_pending_intent(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo

    async def fail_publication(*_args):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", fail_publication)

    task = await tasker.enqueue(name="demo", task_type="demo", payload={"value": 1})

    assert task.status == "pending"
    assert repo.record is not None
    assert repo.record.payload == {"value": 1}


async def test_unique_submit_uses_database_dedupe_and_does_not_republish(monkeypatch):
    repo = FakeRepo()
    tasker = Tasker()
    tasker._repo = repo
    published: list[str] = []

    async def publish(task_id: str):
        published.append(task_id)
        return True

    monkeypatch.setattr(task_service, "get_task_definition", lambda _task_type: FakeDefinition(None))
    monkeypatch.setattr(task_service, "publish_task", publish)

    first, first_created = await tasker.enqueue_unique_by_payload(
        name="demo",
        task_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )
    second, second_created = await tasker.enqueue_unique_by_payload(
        name="demo",
        task_type="demo",
        payload={"kb_id": "kb-1"},
        payload_match={"kb_id": "kb-1"},
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert published == [first.id]


async def test_task_context_throttles_progress_and_rejects_lost_lease(monkeypatch):
    record = make_record(status="running", worker_id="owner")
    record.lease_expires_at = utc_now_naive() + timedelta(seconds=30)
    repo = FakeRepo(record)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    context = TaskContext(record.id, "owner", {"value": 1})

    await context.set_progress(10)
    await context.set_progress(11, "第二步")
    await context.set_progress(12)

    assert context.payload == {"value": 1}
    assert [update["progress"] for update in repo.updates] == [10, 11]
    assert repo.updates[-1]["message"] == "第二步"

    repo.live_owner = False
    with pytest.raises(asyncio.CancelledError, match="lease was lost"):
        await context.set_message("迟到更新")


async def test_task_context_tracks_messages_written_outside_progress_updates(monkeypatch):
    record = make_record(status="running", worker_id="owner")
    repo = FakeRepo(record)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    context = TaskContext(record.id, "owner")

    await context.set_progress(10, "A")
    await context.set_message("B")
    await context.set_progress(11, "A")

    assert repo.updates == [
        {"progress": 10.0, "message": "A"},
        {"message": "B"},
        {"progress": 11.0, "message": "A"},
    ]


async def test_process_task_rebuilds_handler_and_persists_success(monkeypatch):
    record = make_record(payload={"value": 7})
    repo = FakeRepo(record)
    seen: list[int] = []

    async def handler(context: TaskContext):
        seen.append(context.payload["value"])
        await context.set_progress(50, "执行中")
        return {"ok": True}

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert seen == [7]
    assert repo.record.status == "success"
    assert repo.finish_calls[-1]["result"] == {"ok": True}
    assert repo.record.attempt_count == 1


async def test_process_task_requeues_configured_runtime_error(monkeypatch):
    """仅注册的 RuntimeError 会保留清理意图并重新置为 pending。"""
    record = make_record(
        type="knowledge_parent_child_cleanup",
        dedupe_key="cleanup-version-1",
        result={"stale": True},
    )
    repo = FakeRepo(record)

    async def handler(_context: TaskContext):
        """模拟外部 Parent-Child 投影清理失败。"""
        raise RuntimeError("external projection cleanup failed")

    definition = FakeDefinition(
        handler,
        max_attempts=3,
        retryable_exceptions=(RuntimeError,),
    )
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: definition)

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "pending"
    assert repo.record.attempt_count == 1
    assert repo.record.dedupe_key == "cleanup-version-1"
    assert repo.record.result is None
    assert repo.record.error == "external projection cleanup failed"
    assert len(repo.requeue_calls) == 1
    assert repo.requeue_calls[0]["max_attempts"] == 3
    assert repo.finish_calls == []


async def test_process_task_terminalizes_non_retryable_cleanup_error(monkeypatch):
    """非 RuntimeError 的 Parent-Child 清理失败保持原有终态收敛。"""
    record = make_record(type="knowledge_parent_child_cleanup")
    repo = FakeRepo(record)

    async def handler(_context: TaskContext):
        """模拟不可重试的清理参数错误。"""
        raise ValueError("cleanup target is invalid")

    definition = FakeDefinition(
        handler,
        max_attempts=3,
        retryable_exceptions=(RuntimeError,),
    )
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: definition)

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.requeue_calls == []
    assert repo.record.status == "failed"
    assert repo.finish_calls[-1]["error"] == "cleanup target is invalid"


async def test_process_task_terminalizes_exhausted_runtime_error_through_failure_hook(monkeypatch):
    """第三次可重试错误耗尽预算后仍走既有 failure hook 与终态提交。"""
    record = make_record(type="knowledge_parent_child_cleanup", attempt_count=2)
    repo = FakeRepo(record)
    failure_calls: list[str] = []

    async def handler(_context: TaskContext):
        """模拟第三次外部投影清理失败。"""
        raise RuntimeError("cleanup retry budget exhausted")

    async def failure_hook(_session, _record, error: str):
        """记录终态失败 hook 收到的错误摘要。"""
        failure_calls.append(error)

    class ExhaustedDefinition(FakeDefinition):
        """提供带 failure hook 的可重试任务定义。"""

        def load_failure_handler(self):
            """返回用于断言终态路径的测试 hook。"""
            return failure_hook

    definition = ExhaustedDefinition(
        handler,
        max_attempts=3,
        retryable_exceptions=(RuntimeError,),
    )
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: definition)

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.requeue_calls[0]["max_attempts"] == 3
    assert repo.record.status == "failed"
    before_finish = repo.finish_calls[-1]["before_finish"]
    assert before_finish is not None
    await before_finish(object(), record)
    assert failure_calls == ["cleanup retry budget exhausted"]


async def test_process_task_uses_failure_hook_when_success_hook_cannot_load(monkeypatch):
    record = make_record(type="dataset_generation")
    repo = FakeRepo(record)
    failure_calls: list[str] = []

    async def failure_hook(_session, _record, error: str):
        failure_calls.append(error)

    class BrokenSuccessDefinition(FakeDefinition):
        def load_success_handler(self):
            raise ImportError("success hook missing")

        def load_failure_handler(self):
            return failure_hook

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: BrokenSuccessDefinition(None))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "failed"
    assert repo.finish_calls[-1]["message"] == "任务 Handler 无法加载"
    before_finish = repo.finish_calls[-1]["before_finish"]
    assert before_finish is not None
    await before_finish(object(), record)
    assert failure_calls == ["success hook missing"]


async def test_process_task_republishes_pending_tasks_after_slot_release(monkeypatch):
    record = make_record()
    repo = FakeRepo(record)
    publication_limits: list[int] = []

    async def handler(_context: TaskContext):
        return {"ok": True}

    async def publish_pending(*, limit: int = 200) -> list[str]:
        publication_limits.append(limit)
        return []

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))
    monkeypatch.setattr(task_service, "publish_pending_tasks", publish_pending)

    await process_task({"worker_id": "worker-1"}, record.id)

    assert publication_limits == [task_service.DURABLE_TASK_MAX_RUNNING]


async def test_duplicate_delivery_cannot_execute_without_claim(monkeypatch):
    record = make_record(status="running", worker_id="other-owner")
    repo = FakeRepo(record)
    repo.claim_allowed = False
    called = False

    async def handler(context: TaskContext):
        nonlocal called
        called = True

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert called is False
    assert repo.finish_calls == []


async def test_heartbeat_error_cancels_handler_as_lost_lease(monkeypatch):
    record = make_record(timeout_seconds=1)
    repo = FakeRepo(record)
    repo.renew_error = ConnectionError("database unavailable")
    observed_reason: list[str | None] = []

    async def handler(context: TaskContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(task_service, "TASK_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert observed_reason == ["lease_lost"]
    assert repo.finish_calls == []


async def test_parent_job_cancellation_waits_for_handler_exit(monkeypatch):
    """重复取消 worker job 时仍等待 Handler 完成异步收尾。"""

    record = make_record()
    repo = FakeRepo(record)
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    stopped = asyncio.Event()

    async def handler(context: TaskContext):
        """模拟收到取消后仍需完成的异步资源清理。"""

        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            stopped.set()

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    job = asyncio.create_task(process_task({"worker_id": "worker-1"}, record.id))
    try:
        await asyncio.wait_for(started.wait(), 1)
        job.cancel()
        await asyncio.wait_for(cleanup_started.wait(), 1)
        job.cancel()
        await asyncio.sleep(0)

        assert not job.done(), "二次取消不得越过仍在执行的 Handler cleanup"
        assert repo.release_calls == []

        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(job, 1)

        assert stopped.is_set()
        assert repo.release_calls == ["worker_shutdown: worker 停止时任务中断"]
    finally:
        release_cleanup.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_user_cancel_keeps_heartbeat_until_cancelled_terminal_commit(monkeypatch):
    """用户取消 Handler 后，heartbeat 持续到 cancelled 终态提交完成。"""

    record = make_record()
    repo = FakeRepo(record)
    started = asyncio.Event()
    finish_started = asyncio.Event()
    release_finish = asyncio.Event()
    renewals: asyncio.Queue[tuple[bool, bool]] = asyncio.Queue()
    original_renew = repo.renew_lease
    original_finish = repo.finish_owned

    async def handler(_context: TaskContext):
        """保持执行，直到 heartbeat 传递持久取消意图。"""

        started.set()
        await asyncio.Event().wait()

    async def track_renewal(*args, **kwargs):
        """记录每次续租结果，供测试观察取消后的 lease 活性。"""

        result = await original_renew(*args, **kwargs)
        await renewals.put(result)
        return result

    async def block_terminal_commit(*args, **kwargs):
        """阻塞终态提交，模拟跨越 heartbeat 周期的 failure hook。"""

        finish_started.set()
        await release_finish.wait()
        return await original_finish(*args, **kwargs)

    repo.renew_lease = track_renewal
    repo.finish_owned = block_terminal_commit
    monkeypatch.setattr(task_service, "TASK_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    job = asyncio.create_task(process_task({"worker_id": "worker-1"}, record.id))
    try:
        await asyncio.wait_for(started.wait(), 1)
        record.cancel_requested = 1
        while not (await asyncio.wait_for(renewals.get(), 1))[1]:
            pass
        await asyncio.wait_for(finish_started.wait(), 1)

        renewed, cancel_requested = await asyncio.wait_for(renewals.get(), 1)
        assert renewed is True and cancel_requested is True
        assert not job.done()
        assert repo.record.status == "running"

        release_finish.set()
        await asyncio.wait_for(job, 1)

        assert repo.record.status == "cancelled"
        assert repo.finish_calls[-1]["message"] == "任务已取消"
    finally:
        release_finish.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_process_task_timeout_persists_failed_terminal(monkeypatch):
    record = make_record(timeout_seconds=0.01)
    repo = FakeRepo(record)

    observed_reason: list[str | None] = []

    async def handler(context: TaskContext):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observed_reason.append(context.cancellation_reason)
            raise

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    await process_task({"worker_id": "worker-1"}, record.id)

    assert repo.record.status == "failed"
    assert observed_reason == ["timeout"]
    assert repo.finish_calls[-1]["message"] == "任务执行超时"
    assert "0.01-second" in repo.finish_calls[-1]["error"]


async def test_timeout_cleanup_survives_repeated_worker_cancellation(monkeypatch):
    """超时触发的 Handler cleanup 与失败终态不能被后续 worker cancel 打断。"""

    record = make_record(timeout_seconds=0.01)
    repo = FakeRepo(record)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def handler(_context: TaskContext):
        """在 timeout 取消后阻塞异步清理，暴露重复取消竞态。"""

        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    monkeypatch.setattr(task_service, "TaskRepository", lambda: repo)
    monkeypatch.setattr(task_service, "get_task_definition", lambda *_args: FakeDefinition(handler))

    job = asyncio.create_task(process_task({"worker_id": "worker-1"}, record.id))
    try:
        await asyncio.wait_for(cleanup_started.wait(), 1)
        job.cancel()
        job.cancel()
        await asyncio.sleep(0)

        assert not job.done(), "worker cancel 不得打断 timeout cleanup"
        assert repo.finish_calls == []

        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(job, 1)

        assert repo.record.status == "failed"
        assert repo.finish_calls[-1]["message"] == "任务执行超时"
    finally:
        release_cleanup.set()
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)


async def test_pending_cancel_becomes_terminal_without_worker(monkeypatch):
    repo = FakeRepo(make_record())
    tasker = Tasker()
    tasker._repo = repo

    monkeypatch.setattr(task_service, "get_failure_task_definition", lambda *_args: FakeDefinition(None))
    task = await tasker.cancel_task("task-1")

    assert task is not None
    assert task.status == "cancelled"
    assert task.cancel_requested is True
    assert await tasker.delete_task("task-1") is True


async def test_running_cancel_persists_without_loading_failure_hook(monkeypatch):
    """running Task 的取消意图不依赖领域 failure hook 能否导入。"""

    repo = FakeRepo(make_record(status="running", worker_id="owner"))
    tasker = Tasker()
    tasker._repo = repo

    class BrokenFailureDefinition(FakeDefinition):
        """保留已知版本，但让惰性 failure hook 导入失败。"""

        def load_failure_handler(self):
            """模拟当前 worker 未安装可选领域依赖。"""

            raise ImportError("optional handler dependency is unavailable")

    monkeypatch.setattr(
        task_service,
        "get_failure_task_definition",
        lambda *_args: BrokenFailureDefinition(None),
    )

    task = await tasker.cancel_task("task-1")

    assert task is not None
    assert task.status == "running"
    assert task.cancel_requested is True


async def test_unknown_nonlegacy_handler_version_does_not_run_current_failure_hook(monkeypatch):
    record = make_record(type="knowledge_parse", handler_version=2)
    repo = FakeRepo(record)
    tasker = Tasker()
    tasker._repo = repo
    cancel_calls: list[str] = []

    async def cancel_hook(_record):
        cancel_calls.append("called")

    def get_failure_definition(_task_type: str, handler_version: int):
        if handler_version != 1:
            raise ValueError("unsupported version")
        return FakeDefinition(None)

    monkeypatch.setattr(task_service, "get_failure_task_definition", get_failure_definition)
    monkeypatch.setattr(FakeDefinition, "load_failure_handler", lambda _self: cancel_hook)

    assert await tasker.cancel_task(record.id) is None
    assert cancel_calls == []
    assert record.cancel_requested == 0


def test_task_timeout_accepts_existing_values_above_24_hours():
    assert Tasker(default_timeout_seconds=172800.0)._resolve_timeout_seconds(None) == 172800.0


def test_task_timeout_override_cannot_exceed_worker_default():
    tasker = Tasker(default_timeout_seconds=60.0)

    assert tasker._resolve_timeout_seconds(30.0) == 30.0
    with pytest.raises(ValueError, match="cannot exceed the worker default"):
        tasker._resolve_timeout_seconds(61.0)
