"""验证图谱构建请求的文件范围。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuxi.api.routers.knowledge import management


@pytest.fixture
def graph_client(monkeypatch):
    """建立真实路由和可观察的任务提交边界。"""
    app = FastAPI()
    app.include_router(management.knowledge)
    app.dependency_overrides[management.require_knowledge_base_manage] = lambda: SimpleNamespace(uid="user")
    start = AsyncMock(return_value=SimpleNamespace(id="job"))
    monkeypatch.setattr(management.graph_build, "start_graph_build", start)
    return TestClient(app), start


@pytest.mark.parametrize("file_id", [None, "", "  ", 1, False, [], {}])
def test_invalid_explicit_file_id_cannot_submit(graph_client, file_id):
    """显式无效范围不得退化为全库构建。"""
    client, start = graph_client
    response = client.post("/knowledge/knowledge-bases/kb/graph-build/index", json={"file_id": file_id})
    assert response.status_code == 400
    assert response.json() == {"detail": "file_id 必须是非空字符串"}
    start.assert_not_awaited()


@pytest.mark.parametrize("body", [None, {}, {"file_id": "file"}])
def test_graph_build_scope_is_preserved(graph_client, body):
    """空请求保留全库语义，文件请求仅提交指定文件。"""
    client, start = graph_client
    url = "/knowledge/knowledge-bases/kb/graph-build/index"
    response = client.post(url) if body is None else client.post(url, json=body)
    assert response.status_code == 200
    if body and "file_id" in body:
        start.assert_awaited_once_with("kb", file_id="file")
    else:
        start.assert_awaited_once_with("kb")
