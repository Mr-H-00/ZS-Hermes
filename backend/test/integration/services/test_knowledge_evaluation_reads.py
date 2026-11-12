"""真实 HTTP 与独立 PostgreSQL 验证评估读取的事实边界。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from neo4j import GraphDatabase
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.knowledge import require_knowledge_base_read
from yuxi.api.routers.knowledge import evaluation as evaluation_router
from yuxi.api.routers.knowledge import graphs as graph_router
from yuxi.modules.background_jobs import repository as job_repository
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import EvaluationDataset, EvaluationDatasetItem, EvaluationRun, EvaluationRunItem, KnowledgeBase
from yuxi.modules.knowledge.repositories import evaluation as evaluation_repository
from yuxi.modules.knowledge.repositories.evaluation import EvaluationRepository, finish_dataset_generation_job

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def evaluation_database(monkeypatch):
    """每个用例创建并回收自己的评估表，禁止触及共享业务行。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_eval_read")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            for model in (BackgroundJobRecord, KnowledgeBase, EvaluationDataset, EvaluationDatasetItem, EvaluationRun, EvaluationRunItem):
                await connection.run_sync(model.__table__.create)
        monkeypatch.setattr(evaluation_repository, "pg_manager", manager)
        monkeypatch.setattr(job_repository, "pg_manager", manager)
        async with manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id="kb", name="test", kb_type="milvus"))
        yield manager
    finally:
        await drop_isolated_schema(schema, admin, engine)


def _evaluation_app():
    """装配正式 HTTP 路由，仅用固定已授权主体替换认证边界。"""
    app = FastAPI()
    app.include_router(evaluation_router.evaluation, prefix="/api")
    app.dependency_overrides[require_knowledge_base_read] = lambda: object()
    return app


@pytest.mark.parametrize("concurrent_action", ["complete", "resume"])
async def test_dataset_get_cannot_overwrite_committed_owner(evaluation_database, monkeypatch, concurrent_action):
    """HTTP 已读到旧作业后，另一事务完成或关联新作业，GET 不覆盖新事实。"""
    manager = evaluation_database
    metadata = {"source": "generated", "status": "running", "progress": 10, "job_id": "old"}
    async with manager.get_async_session_context() as session:
        session.add(
            BackgroundJobRecord(
                id="old",
                name="old",
                type="dataset_generation",
                status="running",
                progress=90,
                payload={"dataset_id": "dataset", "kb_id": "kb"},
            )
        )
        await EvaluationRepository.create_dataset_in_session(
            session,
            {
                "dataset_id": "dataset",
                "kb_id": "kb",
                "name": "test",
                "build_metadata": metadata,
                "item_count": 1,
            },
        )
    read_started, continue_read = asyncio.Event(), asyncio.Event()
    original_get = job_repository.BackgroundJobRepository.get_by_id

    async def blocked_get(self, job_id):
        """只暂停作业读取返回，不替换数据库事实。"""
        row = await original_get(self, job_id)
        read_started.set()
        await asyncio.wait_for(continue_read.wait(), timeout=10)
        return row

    monkeypatch.setattr(job_repository.BackgroundJobRepository, "get_by_id", blocked_get)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_evaluation_app()), base_url="http://test") as client:
        request = asyncio.create_task(client.get("/api/evaluation/knowledge-bases/kb/datasets"))
        try:
            await asyncio.wait_for(read_started.wait(), timeout=10)
            async with manager.get_async_session_context() as session:
                if concurrent_action == "complete":
                    job = await session.get(BackgroundJobRecord, "old")
                    job.status = "success"
                    await finish_dataset_generation_job(
                        session,
                        job,
                        {
                            "dataset_id": "dataset",
                            "item_count": 1,
                            "build_metadata": {**metadata, "status": "completed", "progress": 100},
                        },
                    )
                else:
                    await EvaluationRepository.attach_dataset_generation_job_in_session(session, "dataset", "new")
            continue_read.set()
            response = await request
            assert response.status_code == 200, response.text
        finally:
            continue_read.set()
            await asyncio.gather(request, return_exceptions=True)
    persisted = await EvaluationRepository().get_dataset("dataset")
    if concurrent_action == "complete":
        assert persisted.build_metadata["status"] == "completed"
        assert persisted.build_metadata["progress"] == 100
    else:
        assert persisted.build_metadata["status"] == "pending"
        assert persisted.build_metadata["job_id"] == "new"


async def test_low_recall_http_includes_old_and_new_empty_retrieval(evaluation_database):
    """空检索有标准片段时列入低召回，缺少标准答案的题目不误报。"""
    async with evaluation_database.get_async_session_context() as session:
        session.add(EvaluationRun(run_id="run_12345678", name="test", kb_id="kb", status="completed", total_items=5, completed_items=5))
        await session.flush()
        for index, gold, retrieved, metrics in [
            (0, ["gold"], [], {}),
            (1, ["gold"], [], {"recall@10": 0}),
            (2, ["gold"], [{"chunk_id": "gold"}], {"recall@10": 1}),
            (3, [], [], {}),
            (4, None, [], {}),
        ]:
            session.add(
                EvaluationRunItem(
                    run_id="run_12345678",
                    item_index=index,
                    query_text=f"question {index}",
                    gold_chunk_ids=gold,
                    retrieved_chunks=retrieved,
                    metrics=metrics,
                )
            )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_evaluation_app()), base_url="http://test") as client:
        response = await client.get(
            "/api/evaluation/knowledge-bases/kb/runs/run_12345678", params={"result_filter": "errors_or_low_recall"}
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["pagination"]["total"] == 2
    assert [item["item_index"] for item in data["items"]] == [0, 1]


@pytest.mark.parametrize("endpoint", ["subgraph", "labels", "stats"])
async def test_graph_http_exposes_unreachable_storage(monkeypatch, endpoint):
    """真实 Neo4j 连接失败经正式 HTTP 路由返回失败，不能伪装为空图。"""
    driver = GraphDatabase.driver("bolt://127.0.0.1:1", auth=None, connection_timeout=0.2, max_transaction_retry_time=0)
    service = KnowledgeGraphService(kb_id="kb", neo4j_connection=SimpleNamespace(driver=driver))

    async def get_service(kb_id):
        """已授权知识库使用本例独占的不可达 Neo4j 连接。"""
        return service

    monkeypatch.setattr(graph_router, "_get_graph_service", get_service)
    app = FastAPI()
    app.include_router(graph_router.graph, prefix="/api")
    app.dependency_overrides[require_knowledge_base_read] = lambda: object()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/graph/{endpoint}", params={"kb_id": "kb"})
        assert response.status_code == 500
        assert "detail" in response.json()
        assert "data" not in response.json()
    finally:
        await asyncio.to_thread(driver.close)
