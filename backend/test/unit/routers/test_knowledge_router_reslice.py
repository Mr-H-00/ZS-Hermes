"""显式重切路由与 Tasker 指纹测试。"""

from types import SimpleNamespace

import pytest

from server.routers import knowledge_router


def test_reslice_fingerprint_is_order_independent():
    """同一组文件和参数只因输入顺序不同也应得到同一指纹。"""
    first = knowledge_router._reslice_fingerprint("kb-1", ["file-2", "file-1"], {"parent_child": {"enabled": True}})
    second = knowledge_router._reslice_fingerprint("kb-1", ["file-1", "file-2"], {"parent_child": {"enabled": True}})
    assert first == second


@pytest.mark.asyncio
async def test_reslice_rejects_file_from_another_kb(monkeypatch):
    """文件属于其它知识库时必须在排队前拒绝。"""
    record = SimpleNamespace(file_id="file-1", kb_id="kb-other", is_folder=False)

    class FakeRepository:
        async def list_by_file_ids(self, _file_ids):
            return [record]

    monkeypatch.setattr(knowledge_router.knowledge_reslice_service, "repository", FakeRepository())
    with pytest.raises(knowledge_router.HTTPException) as error:
        await knowledge_router._validate_reslice_files("kb-1", ["file-1"])
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_enqueue_reslice_delegates_to_service(monkeypatch):
    """路由只把持久提交参数交给重切 service。"""
    captured = {}

    async def enqueue(**kwargs):
        """记录路由交给 service 的参数。"""
        captured.update(kwargs)
        return {
            "task_id": "task-existing",
            "status": "queued",
            "fingerprint": "fingerprint",
            "message": "相同重切任务正在执行",
        }

    monkeypatch.setattr(knowledge_router.knowledge_reslice_service, "enqueue", enqueue)
    result = await knowledge_router._enqueue_reslice_task(
        "kb-1",
        ["file-1"],
        {"indexing_path": "parent_child"},
        "user-1",
        SimpleNamespace(name="KB"),
    )
    assert result["task_id"] == "task-existing"
    assert result["status"] == "queued"
    assert captured == {
        "kb_id": "kb-1",
        "file_ids": ["file-1"],
        "params": {"indexing_path": "parent_child"},
        "operator_id": "user-1",
        "database_name": "KB",
    }


@pytest.mark.asyncio
async def test_enqueue_reslice_maps_active_file_conflict_to_http_409(monkeypatch):
    """活动文件冲突必须通过路由边界返回 HTTP 409。"""

    async def reject_conflict(**_kwargs):
        """模拟 service 在原子提交边界发现文件冲突。"""
        raise knowledge_router.ResliceConflictError("目标文件已有正在执行的重切任务: ['file-1']")

    monkeypatch.setattr(knowledge_router.knowledge_reslice_service, "enqueue", reject_conflict)

    with pytest.raises(knowledge_router.HTTPException) as error:
        await knowledge_router._enqueue_reslice_task(
            "kb-1",
            ["file-1"],
            {"indexing_path": "parent_child"},
            "user-1",
            SimpleNamespace(name="KB"),
        )

    assert error.value.status_code == 409
    assert "file-1" in error.value.detail
