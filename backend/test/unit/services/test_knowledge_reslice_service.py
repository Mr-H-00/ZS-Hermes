"""显式重切任务提交冲突测试。"""

from unittest.mock import AsyncMock

import pytest

from yuxi.services.knowledge_reslice_service import KnowledgeResliceService, ResliceConflictError
from yuxi.services.task_service import Tasker


def _make_service() -> tuple[KnowledgeResliceService, Tasker]:
    """创建不启动 worker、只验证提交边界的重切服务与 Tasker。"""
    tasker = Tasker()
    tasker._repo = AsyncMock()
    service = KnowledgeResliceService(
        manager=object(),
        repository=object(),
        tasker_instance=tasker,
    )
    return service, tasker


@pytest.mark.asyncio
async def test_enqueue_rejects_same_file_with_different_params() -> None:
    """同一文件已有活动任务时，参数不同也必须拒绝第二次提交。"""
    service, tasker = _make_service()
    await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-1"],
        params={"parent_child": {"child_token_num": 200}},
        operator_id="user-1",
        database_name="KB",
    )

    with pytest.raises(ResliceConflictError, match="file-1"):
        await service.enqueue(
            kb_id="kb-1",
            file_ids=["file-1"],
            params={"parent_child": {"child_token_num": 300}},
            operator_id="user-1",
            database_name="KB",
        )

    assert len(tasker._tasks) == 1
    assert tasker._queue.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_rejects_partially_overlapping_file_sets() -> None:
    """文件集合部分重叠时必须拒绝整批新任务，不能并发处理重叠文件。"""
    service, tasker = _make_service()
    params = {"parent_child": {"enabled": True}}
    await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-1", "file-2"],
        params=params,
        operator_id="user-1",
        database_name="KB",
    )

    with pytest.raises(ResliceConflictError, match="file-2"):
        await service.enqueue(
            kb_id="kb-1",
            file_ids=["file-2", "file-3"],
            params=params,
            operator_id="user-1",
            database_name="KB",
        )

    assert len(tasker._tasks) == 1
    assert tasker._queue.qsize() == 1


@pytest.mark.asyncio
async def test_enqueue_reuses_equivalent_fingerprint() -> None:
    """文件顺序不同但 fingerprint 等价时继续复用已有活动任务。"""
    service, tasker = _make_service()
    params = {"parent_child": {"enabled": True}}
    first = await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-2", "file-1"],
        params=params,
        operator_id="user-1",
        database_name="KB",
    )
    second = await service.enqueue(
        kb_id="kb-1",
        file_ids=["file-1", "file-2"],
        params=params,
        operator_id="user-1",
        database_name="KB",
    )

    assert second["task_id"] == first["task_id"]
    assert second["message"] == "相同重切任务正在执行"
    assert len(tasker._tasks) == 1
    assert tasker._queue.qsize() == 1
