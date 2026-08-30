from __future__ import annotations

import asyncio
import uuid
import os
from contextlib import asynccontextmanager

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.live_api_cleanup import (
    make_test_conversation_metadata,
    make_test_conversation_title,
    make_test_resource_id,
)
from yuxi.services.project_service import delete_project_view

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _database_connection():
    """为当前测试 loop 创建独立 PostgreSQL 连接。"""
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")
    connection = await asyncpg.connect(dsn)
    try:
        yield connection
    finally:
        await connection.close()


async def _default_agent_slug(test_client, headers: dict[str, str]) -> str:
    response = await test_client.get("/api/agent", headers=headers)
    assert response.status_code == 200, response.text
    agent = next(item for item in response.json()["agents"] if item.get("is_default"))
    return str(agent.get("slug") or agent["agent_id"])


@pytest_asyncio.fixture()
async def linked_directory(test_client, admin_headers):
    """创建并在用例结束后删除 linked Project 使用的目录。"""

    directory_name = f"pytest-linked-{uuid.uuid4().hex[:10]}"
    response = await test_client.post(
        "/api/workspace/directory",
        headers=admin_headers,
        json={"parent_path": "/", "name": directory_name},
    )
    assert response.status_code == 200, response.text
    try:
        yield directory_name
    finally:
        response = await test_client.request(
            "DELETE",
            "/api/workspace/file",
            headers=admin_headers,
            params={"path": directory_name},
        )
        assert response.status_code in {200, 404}, response.text


async def test_default_thread_creates_implicit_project_with_exclusive_binding(test_client, admin_headers):
    response = await test_client.post(
        "/api/chat/thread",
        headers=admin_headers,
        json={
            "agent_id": await _default_agent_slug(test_client, admin_headers),
            "title": make_test_conversation_title("implicit-project"),
            "metadata": make_test_conversation_metadata("implicit-project"),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["project_id"]
    assert payload["workdir_path"].startswith("projects/")

    async with _database_connection() as db:
        row = await db.fetchrow(
            "SELECT c.project_id, p.selection_status, p.directory_mode, p.workdir_path "
            "FROM conversations c JOIN projects p ON p.id = c.project_id AND p.uid = c.uid "
            "WHERE c.thread_id = $1",
            payload["id"],
        )
    assert row["project_id"] == payload["project_id"]
    assert row["selection_status"] == "implicit"
    assert row["directory_mode"] == "managed"
    assert row["workdir_path"] == payload["workdir_path"]


async def test_linked_project_and_thread_selection_keep_directory_bytes(
    test_client,
    admin_headers,
    linked_directory,
):
    directory_name = linked_directory
    project_response = await test_client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "request_id": make_test_resource_id("linked-project"),
            "name": "Linked",
            "workdir": {"mode": "linked", "path": directory_name},
        },
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()

    thread_response = await test_client.post(
        "/api/chat/thread",
        headers=admin_headers,
        json={
            "agent_id": await _default_agent_slug(test_client, admin_headers),
            "project_id": project["id"],
            "title": make_test_conversation_title("linked-project"),
            "metadata": make_test_conversation_metadata("linked-project"),
        },
    )
    assert thread_response.status_code == 200, thread_response.text
    thread = thread_response.json()
    assert thread["project_id"] == project["id"]
    assert thread["workdir_path"] == directory_name

    rebind = await test_client.put(
        f"/api/chat/thread/{thread['id']}",
        headers=admin_headers,
        json={"project_id": str(uuid.uuid4())},
    )
    assert rebind.status_code == 422, rebind.text

    legacy_direct_path = await test_client.post(
        "/api/chat/thread",
        headers=admin_headers,
        json={
            "agent_id": await _default_agent_slug(test_client, admin_headers),
            "workdir_path": directory_name,
        },
    )
    assert legacy_direct_path.status_code == 422, legacy_direct_path.text

    duplicate = await test_client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "request_id": make_test_resource_id("duplicate-linked-project"),
            "name": "Duplicate",
            "workdir": {"mode": "linked", "path": directory_name},
        },
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] != project["id"]
    assert duplicate.json()["workdir_path"] == directory_name

    invalid_paths = ["/", "../outside", f"{directory_name}/missing"]
    for path in invalid_paths:
        invalid = await test_client.post(
            "/api/projects",
            headers=admin_headers,
            json={
                "request_id": make_test_resource_id("invalid-linked-project"),
                "name": "Invalid",
                "workdir": {"mode": "linked", "path": path},
            },
        )
        assert invalid.status_code in {400, 404}, (path, invalid.text)


async def test_project_rename_and_delete_soft_delete_conversations_but_keep_workdir(
    test_client,
    admin_headers,
    linked_directory,
    standard_user,
):
    project_response = await test_client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "request_id": make_test_resource_id("managed-lifecycle"),
            "name": "Before rename",
            "workdir": {"mode": "linked", "path": linked_directory},
        },
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()

    marker_response = await test_client.post(
        "/api/workspace/directory",
        headers=admin_headers,
        json={"parent_path": f"/{linked_directory}", "name": "keep"},
    )
    assert marker_response.status_code == 200, marker_response.text

    agent_slug = await _default_agent_slug(test_client, admin_headers)
    thread_ids = []
    for suffix in ("one", "two"):
        thread_response = await test_client.post(
            "/api/chat/thread",
            headers=admin_headers,
            json={
                "agent_id": agent_slug,
                "project_id": project["id"],
                "title": make_test_conversation_title(f"project-delete-{suffix}"),
                "metadata": make_test_conversation_metadata(f"project-delete-{suffix}"),
            },
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_ids.append(thread_response.json()["id"])

    cross_user_rename = await test_client.put(
        f"/api/projects/{project['id']}",
        headers=standard_user["headers"],
        json={"name": "Forbidden"},
    )
    assert cross_user_rename.status_code == 404, cross_user_rename.text

    cross_user_delete = await test_client.delete(
        f"/api/projects/{project['id']}",
        headers=standard_user["headers"],
    )
    assert cross_user_delete.status_code == 404, cross_user_delete.text

    rename_response = await test_client.put(
        f"/api/projects/{project['id']}",
        headers=admin_headers,
        json={"name": "After rename"},
    )
    assert rename_response.status_code == 200, rename_response.text
    assert rename_response.json()["name"] == "After rename"

    delete_response = await test_client.delete(
        f"/api/projects/{project['id']}",
        headers=admin_headers,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["deleted_conversations"] == 2

    projects_response = await test_client.get("/api/projects", headers=admin_headers)
    assert projects_response.status_code == 200, projects_response.text
    assert project["id"] not in {item["id"] for item in projects_response.json()}

    threads_response = await test_client.get("/api/chat/threads", headers=admin_headers)
    assert threads_response.status_code == 200, threads_response.text
    assert set(thread_ids).isdisjoint({item["id"] for item in threads_response.json()})

    marker_read = await test_client.get(
        "/api/workspace/tree",
        headers=admin_headers,
        params={"path": f"/{linked_directory}", "include_unbound_project_dirs": True},
    )
    assert marker_read.status_code == 200, marker_read.text
    assert "keep" in {entry["name"] for entry in marker_read.json()["entries"]}

    async with _database_connection() as db:
        project_row = await db.fetchrow(
            "SELECT status, deleted_at FROM projects WHERE id = $1",
            project["id"],
        )
        conversation_rows = await db.fetch(
            "SELECT thread_id, status FROM conversations WHERE project_id = $1 ORDER BY thread_id",
            project["id"],
        )

    assert project_row["status"] == "deleted"
    assert project_row["deleted_at"] is not None
    assert {row["thread_id"] for row in conversation_rows} == set(thread_ids)
    assert {row["status"] for row in conversation_rows} == {"deleted"}

    repeated_delete = await test_client.delete(
        f"/api/projects/{project['id']}",
        headers=admin_headers,
    )
    assert repeated_delete.status_code == 404, repeated_delete.text


async def test_project_delete_waits_for_locked_conversation_creation(
    test_client,
    admin_headers,
    linked_directory,
):
    project_response = await test_client.post(
        "/api/projects",
        headers=admin_headers,
        json={
            "request_id": make_test_resource_id("project-delete-race"),
            "name": "Concurrent lifecycle",
            "workdir": {"mode": "linked", "path": linked_directory},
        },
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()

    dsn = os.environ["POSTGRES_URL"]
    raw_dsn = dsn.replace("+asyncpg", "")
    creator_connection = await asyncpg.connect(raw_dsn)
    engine = create_async_engine(dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    creator_transaction = creator_connection.transaction()
    await creator_transaction.start()
    thread_id = str(uuid.uuid4())

    async def delete_project():
        async with session_factory() as session:
            return await delete_project_view(
                uid=project["uid"],
                project_id=project["id"],
                db=session,
            )

    try:
        locked_project = await creator_connection.fetchrow(
            "SELECT id, uid FROM projects WHERE id = $1 AND uid = $2 FOR UPDATE",
            project["id"],
            project["uid"],
        )
        assert locked_project is not None

        delete_task = asyncio.create_task(delete_project())
        await asyncio.sleep(0.05)
        assert not delete_task.done()

        await creator_connection.execute(
            "INSERT INTO conversations "
            "(thread_id, uid, agent_id, title, status, is_pinned, project_id, extra_metadata) "
            "VALUES ($1, $2, $3, $4, 'active', FALSE, $5, '{}'::json)",
            thread_id,
            project["uid"],
            "default-chatbot",
            make_test_conversation_title("project-delete-race"),
            project["id"],
        )
        await creator_transaction.commit()
        delete_result = await asyncio.wait_for(delete_task, timeout=5)
        assert delete_result["deleted_conversations"] == 1

        async with _database_connection() as database:
            project_status = await database.fetchval("SELECT status FROM projects WHERE id = $1", project["id"])
            conversation_status = await database.fetchval(
                "SELECT status FROM conversations WHERE thread_id = $1",
                thread_id,
            )
        assert project_status == "deleted"
        assert conversation_status == "deleted"
    finally:
        if creator_connection.is_in_transaction():
            await creator_transaction.rollback()
        await creator_connection.close()
        await engine.dispose()
