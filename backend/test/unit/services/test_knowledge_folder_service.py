import pytest

from yuxi.services.knowledge_folder_service import KnowledgeFolderService

pytestmark = pytest.mark.asyncio


class FakeContext:
    """记录迁移任务事务与最终结果。"""

    def __init__(self) -> None:
        """初始化事务计数和结果占位。"""
        self.transactions = 0
        self.result = None

    async def raise_if_cancelled(self) -> None:
        """模拟未取消的任务检查，不返回业务结果。"""
        return None

    async def run_owned_transaction(self, operation) -> None:
        """接收异步操作并用固定会话执行，不返回业务结果。"""
        self.transactions += 1
        await operation("owned-session", object())

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        """接收进度和消息，不返回业务结果。"""
        return None

    async def set_result(self, result: dict) -> None:
        """保存任务结果字典，不返回业务结果。"""
        self.result = result


class FakeRepository:
    """模拟文件迁移批次与统计缓存失效。"""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        """接收共享事件列表并初始化迁移批次。"""
        self.batches = [
            {
                "scanned": 1,
                "processed": 1,
                "created_folders": 1,
                "conflict_file_ids": [],
                "last_file_id": "file-1",
            },
            {"scanned": 0, "processed": 0, "created_folders": 0, "conflict_file_ids": []},
            {"scanned": 0, "processed": 0, "created_folders": 0, "conflict_file_ids": []},
        ]
        self.sessions = []
        self.events = events

    async def detect_virtual_folder_data(self, kb_id: str) -> dict:
        """接收知识库 ID 并返回当前虚拟目录摘要。"""
        if self.batches:
            return {"remaining_steps": 1, "file_count": 1}
        return {"remaining_steps": 0, "file_count": 0}

    async def migrate_virtual_folder_batch(self, session, **kwargs) -> dict:
        """接收事务会话和迁移参数并返回下一批结果。"""
        self.sessions.append(session)
        self.events.append(("migrate", kwargs["kb_id"]))
        return self.batches.pop(0)

    async def invalidate_kb_file_stats_cache(self, kb_id: str) -> None:
        """接收知识库 ID 并记录文件统计缓存失效。"""
        self.events.append(("invalidate", kb_id))


class FakeKnowledgeBaseRepository:
    """模拟持久化知识库统计刷新。"""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        """接收共享事件列表并保存。"""
        self.events = events

    async def refresh_stats(self, kb_id: str) -> object:
        """接收知识库 ID，记录刷新并返回占位对象。"""
        self.events.append(("refresh", kb_id))
        return object()


async def test_virtual_folder_batches_run_inside_task_owned_transaction() -> None:
    """迁移批次提交后只刷新一次持久统计和文件缓存。"""
    events: list[tuple[str, str]] = []
    repository = FakeRepository(events)
    knowledge_base_repository = FakeKnowledgeBaseRepository(events)
    context = FakeContext()

    result = await KnowledgeFolderService(repository, knowledge_base_repository).migrate_virtual_folder_data(
        context,
        kb_id="kb-1",
        operator_id="user-1",
    )

    assert context.transactions == 3
    assert repository.sessions == ["owned-session"] * 3
    assert events == [
        ("migrate", "kb-1"),
        ("migrate", "kb-1"),
        ("migrate", "kb-1"),
        ("refresh", "kb-1"),
        ("invalidate", "kb-1"),
    ]
    assert result == {
        "processed_steps": 1,
        "created_folders": 1,
        "conflict_files": 0,
        "remaining_files": 0,
    }
