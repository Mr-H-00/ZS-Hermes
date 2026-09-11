"""知识库 Durable Task Handler 单元测试。"""

import asyncio
from types import SimpleNamespace

import pytest

from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.services import knowledge_task_service
from yuxi.services.task_registry import get_task_definition


class FakeContext:
    """记录 Handler 通过持久 payload 恢复的执行行为。"""

    def __init__(self, payload: dict) -> None:
        """用持久 payload 与稳定 owner 初始化上下文。"""
        self.payload = payload
        self.task_id = "task-1"
        self.worker_id = "worker-1"
        self.cancellation_checks = 0
        self.result = None

    async def raise_if_cancelled(self) -> None:
        """记录每个文件操作前后的取消检查。"""
        self.cancellation_checks += 1

    async def set_progress(self, _progress: float, _message: str | None = None) -> None:
        """接受 Handler 的进度更新。"""

    async def set_result(self, result: dict) -> None:
        """保存 Handler 的最终结果。"""
        self.result = result


@pytest.mark.asyncio
async def test_reslice_handler_rebuilds_execution_from_persisted_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """注册 Handler 仅凭 payload 恢复操作者、参数和 attempt owner。"""
    calls = []

    class FakeKnowledgeManager:
        """记录重切调用参数。"""

        async def reslice_file(self, kb_id: str, file_id: str, **kwargs):
            """返回一个已索引文件结果。"""
            calls.append((kb_id, file_id, kwargs))
            return {"file_id": file_id, "status": "indexed"}

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    context = FakeContext(
        {
            "kb_id": "kb-1",
            "file_ids": ["file-1"],
            "params": {"parent_child": {"enabled": True}},
            "operator_id": "user-1",
            "fingerprint": "stored",
        }
    )

    handler = get_task_definition("knowledge_reslice").load_handler()
    result = await handler(context)

    assert calls == [
        (
            "kb-1",
            "file-1",
            {
                "operator_id": "user-1",
                "params": {"parent_child": {"enabled": True}},
                "processing_task_id": "task-1",
                "processing_owner": "worker-1",
            },
        )
    ]
    assert result == {"items": [{"file_id": "file-1", "status": "indexed"}], "processed": 1, "failed": 0}
    assert context.result == result
    assert context.cancellation_checks == 2


@pytest.mark.asyncio
async def test_parent_child_cleanup_handler_rebuilds_persisted_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理 Handler 只凭持久 payload 定位 executor 与 superseded 版本。"""
    cleanup_calls = []

    class FakeExecutor:
        """记录幂等旧版本清理调用。"""

        async def cleanup_superseded_parent_child_version(
            self,
            kb_id: str,
            file_id: str,
            version_id: str,
            *,
            control_check,
        ):
            """返回已清理的持久目标。"""
            del control_check
            cleanup_calls.append((kb_id, file_id, version_id))
            return {"version_id": version_id, "status": "cleaned"}

    class FakeKnowledgeManager:
        """按知识库返回支持 Parent-Child 清理的执行器。"""

        async def get_kb_executor(self, kb_id: str):
            """确认 payload 中的知识库标识。"""
            assert kb_id == "kb-1"
            return FakeExecutor()

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    context = FakeContext({"kb_id": "kb-1", "file_id": "file-1", "version_id": "version-old"})

    handler = get_task_definition("knowledge_parent_child_cleanup").load_handler()
    result = await handler(context)

    assert cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert result == {"version_id": "version-old", "status": "cleaned"}
    assert context.result == result
    assert context.cancellation_checks == 2


@pytest.mark.asyncio
async def test_parent_child_cleanup_handler_stops_after_graph_when_lease_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durable Task 的 fence 在图谱删除后失效时不得继续删除 Milvus 或 PostgreSQL 投影。"""
    graph_cleanup_calls = []

    class LeaseLostAfterGraphContext(FakeContext):
        """在清理函数的图谱后 fence 模拟当前 attempt 丢失 lease。"""

        async def raise_if_cancelled(self) -> None:
            """第三次控制检查时拒绝过期 owner。"""
            self.cancellation_checks += 1
            if self.cancellation_checks == 3:
                raise asyncio.CancelledError("Task lease was lost")

    class FakeRepository:
        """记录生产清理器对 superseded 版本的 PostgreSQL 删除。"""

        def __init__(self) -> None:
            """准备属于指定文件的 superseded 版本。"""
            self.deleted_versions = []
            self.version = SimpleNamespace(
                kb_id="kb-1",
                file_id="file-1",
                version_id="version-old",
                embedding_dimension=4,
                status="superseded",
            )

        async def get_version(self, _version_id: str):
            """返回清理目标的持久化版本事实。"""
            return self.version

        async def delete_version(self, version_id: str) -> None:
            """记录 PostgreSQL 版本删除。"""
            self.deleted_versions.append(version_id)

    class FakeCollection:
        """记录生产 Milvus 删除器的调用。"""

        def __init__(self) -> None:
            """初始化空删除记录。"""
            self.deleted_expressions = []

        def delete(self, expression: str) -> None:
            """记录同步 Milvus 删除表达式。"""
            self.deleted_expressions.append(expression)

    class FakeGraphService:
        """记录生产清理器完成的第一段图谱删除。"""

        async def delete_parent_child_version_graph(self, kb_id: str, file_id: str, version_id: str) -> None:
            """完成图谱投影删除后交还控制权给下一道 fence。"""
            graph_cleanup_calls.append((kb_id, file_id, version_id))

    repository = FakeRepository()
    collection = FakeCollection()
    executor = MilvusKB.__new__(MilvusKB)
    monkeypatch.setattr(milvus_module, "KnowledgeParentChildChunkRepository", lambda: repository)
    monkeypatch.setattr(executor, "_get_existing_child_collection", lambda _dimension: collection)
    monkeypatch.setattr(milvus_module, "invalidate_parent_version", lambda *_args: _async_value(None))
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService",
        FakeGraphService,
    )

    class FakeKnowledgeManager:
        """返回需要受当前 Task 控制的 Parent-Child 清理执行器。"""

        async def get_kb_executor(self, _kb_id: str):
            """提供测试执行器。"""
            return executor

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    context = LeaseLostAfterGraphContext({"kb_id": "kb-1", "file_id": "file-1", "version_id": "version-old"})

    with pytest.raises(asyncio.CancelledError, match="lease was lost"):
        await knowledge_task_service.run_knowledge_parent_child_cleanup(context)

    assert graph_cleanup_calls == [("kb-1", "file-1", "version-old")]
    assert collection.deleted_expressions == []
    assert repository.deleted_versions == []
    assert context.result is None
    assert context.cancellation_checks == 3


async def _async_value(value):
    """返回异步测试替身结果。"""
    return value


@pytest.mark.asyncio
async def test_ingest_forwards_custom_indexing_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """自动入库保留父子块与嵌入特征参数。"""
    indexed_params = []

    class FakeKnowledgeManager:
        """模拟添加、解析和索引的完整轻量链路。"""

        async def add_file_record(self, _kb_id: str, _item: str, **_kwargs):
            """返回新文件元数据。"""
            return {"file_id": "file-1", "status": "uploaded"}

        async def parse_file(self, _kb_id: str, _file_id: str, **_kwargs):
            """返回可入库的解析结果。"""
            return {"file_id": "file-1", "status": "parsed"}

        async def update_file_params(self, _kb_id: str, _file_id: str, params: dict, **_kwargs):
            """记录自动入库前保存的参数。"""
            indexed_params.append(("update", params))

        async def index_file(self, _kb_id: str, _file_id: str, *, params: dict, **_kwargs):
            """记录传给索引器的参数。"""
            indexed_params.append(("index", params))
            return {"file_id": "file-1", "status": "indexed"}

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    custom_params = {
        "parent_child": {"enabled": True},
        "embedding_features": {"bge_m3_sparse_enabled": True},
    }
    context = FakeContext(
        {
            "kb_id": "kb-1",
            "items": ["minio://bucket/file.pdf"],
            "params": {"auto_index": True, **custom_params},
            "operator_id": "user-1",
        }
    )

    await knowledge_task_service.run_knowledge_ingest(context)

    assert indexed_params == [("index", custom_params)]


@pytest.mark.asyncio
async def test_index_handler_persists_params_only_after_index_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通入库不能在 index_file 取得状态执行权之前单独改写参数。"""
    calls = []

    class FakeKnowledgeManager:
        """拒绝索引前的独立参数写入并记录索引调用。"""

        async def update_file_params(self, *_args, **_kwargs):
            """在索引认领前写入参数时让测试失败。"""
            raise AssertionError("入库状态认领前不得单独写 processing_params")

        async def index_file(self, kb_id: str, file_id: str, **kwargs):
            """记录应原子认领并保存参数的索引调用。"""
            calls.append((kb_id, file_id, kwargs))
            return {"file_id": file_id, "status": "indexed"}

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    context = FakeContext(
        {
            "kb_id": "kb-1",
            "file_ids": ["file-1"],
            "params": {"parent_child": {"enabled": True}},
            "operator_id": "user-1",
        }
    )

    result = await knowledge_task_service.run_knowledge_index(context)

    assert result["failed"] == 0
    assert calls == [
        (
            "kb-1",
            "file-1",
            {
                "operator_id": "user-1",
                "params": {"parent_child": {"enabled": True}},
                "processing_task_id": "task-1",
                "processing_owner": "worker-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_parse_handler_persists_params_with_owner_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析参数必须随 owner 传给 parse_file，不能先走无栅栏更新。"""
    calls = []

    class FakeKnowledgeManager:
        """拒绝解析前的独立参数写入并记录解析调用。"""

        async def update_file_params(self, *_args, **_kwargs):
            """在解析认领前写入参数时让测试失败。"""
            raise AssertionError("解析状态认领前不得单独写 processing_params")

        async def parse_file(self, kb_id: str, file_id: str, **kwargs):
            """记录应原子认领并保存参数的解析调用。"""
            calls.append((kb_id, file_id, kwargs))
            return {"file_id": file_id, "status": "parsed"}

    monkeypatch.setattr(knowledge_task_service, "knowledge_base", FakeKnowledgeManager())
    context = FakeContext(
        {
            "kb_id": "kb-1",
            "file_ids": ["file-1"],
            "params": {"chunk_parser_config": {"chunk_token_num": 256}},
            "operator_id": "user-1",
        }
    )

    result = await knowledge_task_service.run_knowledge_parse(context)

    assert result["failed"] == 0
    assert calls == [
        (
            "kb-1",
            "file-1",
            {
                "operator_id": "user-1",
                "params": {"chunk_parser_config": {"chunk_token_num": 256}},
                "processing_task_id": "task-1",
                "processing_owner": "worker-1",
            },
        )
    ]
