"""显式重切任务的持久提交与冲突测试。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.services import knowledge_reslice_service as service_module
from yuxi.services.knowledge_reslice_service import KnowledgeResliceService, ResliceConflictError
from yuxi.services.task_service import Task


class FakeRecord(SimpleNamespace):
    """提供 TaskRecord 到公开字典的最小测试替身。"""

    def to_dict(self) -> dict:
        """返回构造持久 Task 所需的字段。"""
        return vars(self).copy()


class FakeTaskRepository:
    """记录原子提交边界内的活动任务查询。"""

    def __init__(self, active_tasks: list[FakeRecord] | None = None) -> None:
        """用给定活动任务初始化仓储替身。"""
        self.active_tasks = active_tasks or []
        self.lock_scopes: list[str] = []

    async def lock_submission_scope_in_session(self, session, scope: str) -> None:
        """记录当前知识库的事务锁范围。"""
        assert session == "session"
        self.lock_scopes.append(scope)

    async def list_active_by_payload_in_session(self, session, *, task_type: str, payload_match: dict):
        """返回测试预置的活动 TaskRecord。"""
        assert session == "session"
        assert task_type == "knowledge_reslice"
        assert payload_match == {"kb_id": "kb-1"}
        return self.active_tasks


class FakeTasker:
    """记录事务内创建和提交后发布顺序。"""

    def __init__(self, events: list[str]) -> None:
        """共享事件列表以观测 commit 与 publish 顺序。"""
        self.events = events
        self.created_payload: dict | None = None

    async def create_unique_in_session(self, session, **kwargs):
        """创建尚未发布的持久任务替身。"""
        assert session == "session"
        self.events.append("create")
        self.created_payload = kwargs["payload"]
        return Task(id="task-new", name=kwargs["name"], type=kwargs["task_type"], payload=kwargs["payload"]), True

    async def publish(self, task: Task) -> None:
        """记录提交后的任务发布。"""
        assert task.id == "task-new"
        self.events.append("publish")


def make_active_task(*, task_id: str, file_ids: list[str], fingerprint: str) -> FakeRecord:
    """构造可由 Task.from_dict 恢复的活动任务记录。"""
    return FakeRecord(
        id=task_id,
        name="文档重切 (KB)",
        type="knowledge_reslice",
        status="running",
        payload={"kb_id": "kb-1", "file_ids": file_ids, "fingerprint": fingerprint},
    )


def make_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    active_tasks: list[FakeRecord] | None = None,
) -> tuple[KnowledgeResliceService, FakeTasker, FakeTaskRepository, list[str]]:
    """创建只覆盖 PostgreSQL 提交边界的重切服务。"""
    events: list[str] = []

    @asynccontextmanager
    async def session_context():
        """模拟正常退出时提交事务。"""
        yield "session"
        events.append("commit")

    monkeypatch.setattr(service_module.pg_manager, "get_async_session_context", session_context)
    task_repository = FakeTaskRepository(active_tasks)
    tasker = FakeTasker(events)
    service = KnowledgeResliceService(
        repository=object(),
        task_repository=task_repository,
        tasker_instance=tasker,
    )
    return service, tasker, task_repository, events


@pytest.mark.asyncio
async def test_enqueue_persists_operator_and_publishes_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """新任务持久化操作者，并且事务提交后才发布。"""
    service, tasker, task_repository, events = make_service(monkeypatch)

    result = await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-1"],
        params={"parent_child": {"enabled": True}},
        operator_id="user-1",
        database_name="KB",
    )

    assert result["task_id"] == "task-new"
    assert tasker.created_payload is not None
    assert tasker.created_payload["operator_id"] == "user-1"
    assert task_repository.lock_scopes == ["knowledge-reslice:kb-1"]
    assert events == ["create", "commit", "publish"]


@pytest.mark.asyncio
async def test_enqueue_reuses_equivalent_fingerprint_without_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """文件顺序不同但指纹等价时复用已有活动任务。"""
    params = {"parent_child": {"enabled": True}}
    fingerprint = KnowledgeResliceService.fingerprint("kb-1", ["file-2", "file-1"], params)
    active = make_active_task(task_id="task-existing", file_ids=["file-2", "file-1"], fingerprint=fingerprint)
    service, _tasker, _task_repository, events = make_service(monkeypatch, active_tasks=[active])

    result = await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-1", "file-2"],
        params=params,
        operator_id="user-1",
        database_name="KB",
    )

    assert result["task_id"] == "task-existing"
    assert result["message"] == "相同重切任务正在执行"
    assert events == ["commit"]


@pytest.mark.asyncio
async def test_enqueue_rejects_partially_overlapping_file_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    """同知识库活动任务与新请求部分重叠时拒绝整批提交。"""
    active = make_active_task(task_id="task-existing", file_ids=["file-1", "file-2"], fingerprint="other")
    service, _tasker, _task_repository, events = make_service(monkeypatch, active_tasks=[active])

    with pytest.raises(ResliceConflictError, match="file-2"):
        await service.enqueue(
            kb_id="kb-1",
            file_ids=["file-2", "file-3"],
            params={"parent_child": {"enabled": True}},
            operator_id="user-1",
            database_name="KB",
        )

    assert events == []


@pytest.mark.asyncio
async def test_enqueue_allows_disjoint_file_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    """同知识库中互不重叠的文件集合可创建新任务。"""
    active = make_active_task(task_id="task-existing", file_ids=["file-1"], fingerprint="other")
    service, _tasker, _task_repository, events = make_service(monkeypatch, active_tasks=[active])

    result = await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-2"],
        params={},
        operator_id="user-1",
        database_name="KB",
    )

    assert result["task_id"] == "task-new"
    assert events == ["create", "commit", "publish"]
