"""
Integration tests for API Key router endpoints.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.identity.models import APIKey, OIDCIdentity, User
from yuxi.modules.identity.repositories.oidc import OIDCRepository
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import PermissionDeniedError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

API_KEYS_PATH = "/api/user/apikey/"
# 受登录保护的轻量端点，用于验证 Bearer 鉴权（API Key / JWT）是否生效，无需执行智能体
PROTECTED_PATH = "/api/agent"


def _create_payload(name: str) -> dict[str, str]:
    return {"request_id": str(uuid.uuid4()), "name": name}


async def test_list_api_keys_requires_auth(test_client):
    """List API keys should require authentication."""
    response = await test_client.get(API_KEYS_PATH)
    assert response.status_code == 401


async def test_list_api_keys_requires_admin(test_client, admin_headers):
    """List API keys should require admin privileges."""
    response = await test_client.get(API_KEYS_PATH, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_keys" in data
    assert "total" in data


async def test_create_api_key(test_client, admin_headers):
    """Admin should be able to create a new API key."""
    payload = _create_payload("Test API Key")
    response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_key" in data
    assert "secret" in data
    assert data["api_key"]["name"] == "Test API Key"
    assert data["api_key"]["key_prefix"].startswith("yxkey_")
    assert data["secret"].startswith(data["api_key"]["key_prefix"])

    replay = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == data

    conflict = await test_client.post(
        API_KEYS_PATH,
        json={**payload, "name": "Different intent"},
        headers=admin_headers,
    )
    assert conflict.status_code == 409, conflict.text


async def test_concurrent_create_replays_one_committed_api_key(test_client, admin_headers):
    """同一幂等请求并发到达时只提交一行，并向双方发布同一 secret。"""

    payload = _create_payload("Concurrent Replay")
    responses = await asyncio.gather(
        test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers),
        test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers),
    )

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    created = responses[0].json()

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            row_count = await db.scalar(select(func.count(APIKey.id)).where(APIKey.request_id == payload["request_id"]))
        assert row_count == 1
    finally:
        await engine.dispose()
        await test_client.delete(f"{API_KEYS_PATH}{created['api_key']['id']}", headers=admin_headers)


async def test_get_api_key(test_client, admin_headers):
    """Admin should be able to get a single API key."""
    # First create a key
    create_response = await test_client.post(API_KEYS_PATH, json=_create_payload("Get Test"), headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Then retrieve it
    response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["id"] == created["id"]
    assert data["api_key"]["name"] == "Get Test"


async def test_update_api_key(test_client, admin_headers):
    """Admin should be able to update an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json=_create_payload("Update Test"), headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Update it
    response = await test_client.put(
        f"{API_KEYS_PATH}{created['id']}",
        json={"name": "Updated Name", "is_enabled": False},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["name"] == "Updated Name"
    assert data["api_key"]["is_enabled"] is False


async def test_original_creation_intent_remains_replayable_after_mutable_fields_change(test_client, admin_headers):
    payload = _create_payload("Original Intent")
    create_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()

    update_response = await test_client.put(
        f"{API_KEYS_PATH}{created['api_key']['id']}",
        json={"name": "Renamed Later"},
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text

    replay_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert replay_response.status_code == 200, replay_response.text
    assert replay_response.json()["secret"] == created["secret"]
    assert replay_response.json()["api_key"]["id"] == created["api_key"]["id"]
    assert replay_response.json()["api_key"]["name"] == "Renamed Later"

    await test_client.delete(f"{API_KEYS_PATH}{created['api_key']['id']}", headers=admin_headers)


async def test_delete_api_key(test_client, admin_headers):
    """Admin should be able to delete an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json=_create_payload("Delete Test"), headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Delete it
    response = await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    # Verify it's gone
    get_response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert get_response.status_code == 404


async def test_revoked_api_key_cannot_be_resurrected_by_replaying_creation_request(test_client, admin_headers):
    payload = _create_payload("Revocation Tombstone")
    create_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    secret = create_response.json()["secret"]
    api_key_id = create_response.json()["api_key"]["id"]

    delete_response = await test_client.delete(f"{API_KEYS_PATH}{api_key_id}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text

    replay_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert replay_response.status_code == 409, replay_response.text
    assert "secret" not in replay_response.text.lower()

    auth_response = await test_client.get(PROTECTED_PATH, headers={"Authorization": f"Bearer {secret}"})
    assert auth_response.status_code == 401, auth_response.text


async def test_user_delete_blocks_oidc_login_and_old_api_key(test_client, admin_headers):
    """删除用户阻断已绑定 OIDC 登录，已撤销 Key 不能重发或启用。"""

    suffix = uuid.uuid4().hex[:12]
    user_response = await test_client.post(
        "/api/auth/users",
        json={"username": f"revive_{suffix}", "password": "routerTest123!", "role": "user"},
        headers=admin_headers,
    )
    assert user_response.status_code == 200, user_response.text
    user_id = user_response.json()["id"]
    payload = {**_create_payload("Deleted User Tombstone"), "user_id": user_id}
    create_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()
    api_key_id = created["api_key"]["id"]
    secret = created["secret"]
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db:
            db.add(OIDCIdentity(issuer="https://identity.example.test", subject=f"test:{suffix}", user_id=user_id))
            await db.commit()
        delete_response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text

        async with factory() as db:
            deleted_user = await db.get(User, user_id)
            persisted_key = await db.get(APIKey, api_key_id)
            assert deleted_user is not None and deleted_user.is_deleted == 1
            assert persisted_key is not None and persisted_key.is_enabled is False
            assert persisted_key.revoked_at is not None
            with pytest.raises(PermissionDeniedError):
                await OIDCRepository(db).resolve_user(
                    SimpleNamespace(issuer_url="https://identity.example.test"),
                    {"sub": f"test:{suffix}"},
                    {},
                )

        list_response = await test_client.get(API_KEYS_PATH, headers=admin_headers)
        assert list_response.status_code == 200, list_response.text
        assert api_key_id not in {item["id"] for item in list_response.json()["api_keys"]}

        replay_response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
        assert replay_response.status_code == 404, replay_response.text
        assert "secret" not in replay_response.json()

        enable_response = await test_client.put(
            f"{API_KEYS_PATH}{api_key_id}",
            json={"is_enabled": True},
            headers=admin_headers,
        )
        assert enable_response.status_code == 404, enable_response.text

        auth_response = await test_client.get(PROTECTED_PATH, headers={"Authorization": f"Bearer {secret}"})
        assert auth_response.status_code == 401, auth_response.text

        async with factory() as db:
            persisted_key = await db.get(APIKey, api_key_id)
            assert persisted_key is not None and persisted_key.revoked_at is not None
            assert persisted_key.is_enabled is False
    finally:
        async with factory() as db:
            await db.execute(delete(APIKey).where(APIKey.id == api_key_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()


async def test_api_key_auth_protected_endpoint(test_client, admin_headers):
    """Test that API Key can be used to authenticate to a protected endpoint via Bearer token."""
    # Create an API key
    create_response = await test_client.post(API_KEYS_PATH, json=_create_payload("Auth Test"), headers=admin_headers)
    assert create_response.status_code == 200
    api_key_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    try:
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert response.status_code == 200, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_api_key_auth_requires_valid_key(test_client):
    """Test that invalid API Key is rejected."""
    # Call protected endpoint with invalid API Key
    response = await test_client.get(
        PROTECTED_PATH,
        headers={"Authorization": "Bearer yxkey_invalid_key_that_does_not_exist"},
    )
    assert response.status_code == 401, response.text


async def test_api_key_auth_requires_bearer_prefix(test_client, admin_headers):
    """Test that API Key must be prefixed with 'Bearer '."""
    # Create an API key
    admin_response = await test_client.post(API_KEYS_PATH, json=_create_payload("Prefix Test"), headers=admin_headers)
    assert admin_response.status_code == 200
    api_key_secret = admin_response.json()["secret"]
    created = admin_response.json()["api_key"]

    try:
        # Call without Bearer prefix should fail
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": api_key_secret},  # Missing "Bearer " prefix
        )
        assert response.status_code == 401, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


@pytest.mark.self_managed_resources
async def test_api_key_auto_binds_to_current_user(test_client, admin_headers):
    """通过 HTTP 回读创建者归属，并在 finally 清理本例创建的 Key。"""
    current_user = await test_client.get("/api/auth/me", headers=admin_headers)
    assert current_user.status_code == 200, current_user.text
    creator_id = current_user.json()["id"]

    create_response = await test_client.post(
        API_KEYS_PATH,
        json=_create_payload("Auto Bind Test"),
        headers=admin_headers,
    )
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    try:
        assert created["user_id"] == creator_id
        response = await test_client.get(API_KEYS_PATH, headers=admin_headers)
        assert response.status_code == 200, response.text
        saved = next(key for key in response.json()["api_keys"] if key["id"] == created["id"])
        assert saved["user_id"] == creator_id
    finally:
        deleted = await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


APP_OVERVIEW_PATH = "/api/user/apikey/apps"


async def test_app_overview_requires_auth(test_client):
    """APP 概览只对已认证用户开放。"""
    response = await test_client.get(APP_OVERVIEW_PATH)
    assert response.status_code == 401, response.text


@pytest.mark.self_managed_resources
@pytest.mark.parametrize("role", ["user", "admin"])
async def test_app_overview_rejects_non_superadmin(test_client, admin_headers, role):
    """已绑定部门的普通用户和管理员都在超管权限边界被拒绝。"""
    suffix = uuid.uuid4().hex[:8]
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200, me.text
    created_user = await test_client.post(
        "/api/auth/users",
        json={
            "username": f"overview_{suffix}",
            "password": "overviewTest123!",
            "role": role,
            "department_id": me.json()["department_id"],
        },
        headers=admin_headers,
    )
    assert created_user.status_code == 200, created_user.text
    user_id = created_user.json()["id"]
    uid = created_user.json()["uid"]

    try:
        login = await test_client.post(
            "/api/auth/token",
            data={"username": uid, "password": "overviewTest123!"},
        )
        assert login.status_code == 200, login.text
        user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        allowed = await test_client.get(API_KEYS_PATH, headers=user_headers)
        assert allowed.status_code == 200, allowed.text

        response = await test_client.get(APP_OVERVIEW_PATH, headers=user_headers)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "仅超级管理员可查看 API 集成概览"
    finally:
        deleted = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


@pytest.mark.self_managed_resources
async def test_app_overview_counts_app_keys_and_end_users(test_client, admin_headers, postgres_sessions):
    """真实 HTTP 汇总超过 500 个 Key，精确排除无效凭据、停用用户和协作输入。"""
    app_id = f"overview-app-{uuid.uuid4().hex[:8]}"
    older_app_id = f"{app_id}-older"
    baseline = await test_client.get(APP_OVERVIEW_PATH, headers=admin_headers)
    assert baseline.status_code == 200, baseline.text
    create_response = await test_client.post(
        API_KEYS_PATH,
        json={**_create_payload("Overview App Key"), "access_level": "agents", "app_id": app_id},
        headers=admin_headers,
    )
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()["api_key"]
    request_prefix = uuid.uuid4().hex
    user_uids = [f"overview-user-{request_prefix}-{index}" for index in range(5)]
    thread_ids = [uuid.uuid4().hex for _ in range(2)]
    project_ids = [uuid.uuid4().hex for _ in range(2)]

    try:
        async with postgres_sessions() as db:
            # 首次签发经真实 HTTP；批量凭据和已取消输入只准备只读统计样本，不启动 worker。
            for index in range(502):
                db.add(
                    APIKey(
                        key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                        key_prefix="yxkey_fixture",
                        request_id=f"{request_prefix}-{index}",
                        name=f"Overview fixture {index}",
                        user_id=created["user_id"],
                        app_id=older_app_id if index == 0 else app_id,
                        access_level="agents",
                        created_by="pytest",
                        created_at=utc_now() - timedelta(days=1) if index == 0 else utc_now(),
                    )
                )
            for label, updates in [
                ("disabled", {"is_enabled": False}),
                ("expired", {"expires_at": utc_now() - timedelta(days=1)}),
                ("revoked", {"revoked_at": utc_now(), "is_enabled": False}),
                ("unbound", {"app_id": None}),
            ]:
                db.add(
                    APIKey(
                        key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                        key_prefix="yxkey_fixture",
                        request_id=f"{request_prefix}-{label}",
                        name=label,
                        user_id=created["user_id"],
                        app_id=updates.pop("app_id", app_id),
                        access_level="full",
                        created_by="pytest",
                        **updates,
                    )
                )
            user_specs = [
                ("end_user", app_id, False),
                ("end_user", app_id, False),
                ("end_user", app_id, True),
                ("end_user", older_app_id, False),
                ("human", None, True),
            ]
            for index, (user_kind, user_app_id, is_deleted) in enumerate(user_specs):
                uid = user_uids[index]
                db.add(
                    User(
                        username=uid,
                        uid=uid,
                        password_hash="not-a-login-credential",
                        role="user",
                        user_kind=user_kind,
                        owner_user_id=created["user_id"] if user_kind == "end_user" else None,
                        app_id=user_app_id,
                        end_user_id=str(index) if user_kind == "end_user" else None,
                        is_deleted=int(is_deleted),
                    )
                )
            await db.flush()
            deleted_owner = await db.scalar(select(User).where(User.uid == user_uids[4]))
            db.add(
                APIKey(
                    key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    key_prefix="yxkey_fixture",
                    request_id=f"{request_prefix}-deleted-owner",
                    name="deleted-owner",
                    user_id=deleted_owner.id,
                    app_id=app_id,
                    access_level="agents",
                    created_by="pytest",
                )
            )
            for index, user_index in enumerate([0, 3]):
                db.add(
                    Project(
                        id=project_ids[index],
                        uid=user_uids[user_index],
                        name="Overview fixture",
                        selection_status="implicit",
                        workdir_path="projects/overview-fixture",
                        directory_mode="managed",
                    )
                )
                await db.flush()
                db.add(
                    Session(
                        thread_id=thread_ids[index],
                        uid=user_uids[user_index],
                        app_id=app_id if index == 0 else older_app_id,
                        agent_id="overview-fixture",
                        project_id=project_ids[index],
                    )
                )
            await db.flush()
            for index, source in enumerate(["chat", "public_api", "cooperation", "chat"]):
                other_app = index == 3
                db.add(
                    AgentInput(
                        id=uuid.uuid4().hex,
                        thread_id=thread_ids[int(other_app)],
                        uid=user_uids[3 if other_app else 0],
                        app_id=older_app_id if other_app else app_id,
                        agent_slug="overview-fixture",
                        kind="follow_up",
                        status="cancelled",
                        messages=[{"type": "text", "text": "fixture"}],
                        source=source,
                        channel="internal" if source == "cooperation" else "api",
                        cancelled_at=utc_now(),
                    )
                )
            await db.commit()

        response = await test_client.get(APP_OVERVIEW_PATH, headers=admin_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        app_row = next(row for row in data["apps"] if row["app_id"] == app_id)
        older_app = next(row for row in data["apps"] if row["app_id"] == older_app_id)
        assert app_row["end_user_count"] == 2
        assert app_row["input_count"] == 2
        assert len(app_row["keys"]) == 504
        assert sum(key["is_valid"] for key in app_row["keys"]) == 502
        assert {key["name"] for key in app_row["keys"] if not key["is_valid"]} == {"disabled", "expired"}
        assert len(older_app["keys"]) == 1
        assert older_app["end_user_count"] == older_app["input_count"] == 1
        expected = {"app_count": 2, "key_count": 503, "end_user_count": 3, "input_count": 3}
        for name, increase in expected.items():
            assert data["totals"][name] == baseline.json()["totals"][name] + increase
    finally:
        async with postgres_sessions() as db:
            await db.execute(delete(AgentInput).where(AgentInput.thread_id.in_(thread_ids)))
            await db.execute(delete(Session).where(Session.thread_id.in_(thread_ids)))
            await db.execute(delete(Project).where(Project.id.in_(project_ids)))
            await db.execute(delete(APIKey).where(APIKey.request_id.startswith(request_prefix)))
            await db.execute(delete(User).where(User.uid.in_(user_uids)))
            await db.commit()
        deleted = await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_agents_key_app_update_requires_explicit_null(test_client, admin_headers, api_key_factory, postgres_sessions):
    """省略 APP 保留绑定；显式 null 切换本人，历史会话不迁移且原创建意图仍可重放。"""
    from test.integration.api.helpers import delete_created_threads

    app_id = f"edit-scope-{uuid.uuid4().hex}"
    payload = {**_create_payload("Editable agents key"), "access_level": "agents", "app_id": app_id}
    issued = await api_key_factory(payload)
    thread_id = None
    try:
        created = await test_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": "default-chatbot"},
            headers={**issued.headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        async with postgres_sessions() as db:
            original = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            original_binding = (original.uid, original.app_id, original.project_id)

        for updates in ({"name": "Renamed agents key"}, {"access_level": "agents"}):
            response = await test_client.put(f"{API_KEYS_PATH}{issued.key_id}", json=updates, headers=admin_headers)
            assert response.status_code == 200, response.text
            async with postgres_sessions() as db:
                assert (await db.get(APIKey, issued.key_id)).app_id == app_id
            visible = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=issued.headers)
            assert visible.status_code == 200 and visible.json()["id"] == thread_id

        switched = await test_client.put(f"{API_KEYS_PATH}{issued.key_id}", json={"app_id": None}, headers=admin_headers)
        assert switched.status_code == 200, switched.text
        async with postgres_sessions() as db:
            key = await db.get(APIKey, issued.key_id)
            stored = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            assert key.app_id is None and key.access_level == "agents"
            assert (stored.uid, stored.app_id, stored.project_id) == original_binding
        hidden = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=issued.headers)
        assert hidden.status_code == 404, hidden.text

        replay = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["secret"] == issued.response.json()["secret"]
        assert replay.json()["api_key"]["app_id"] is None
        async with postgres_sessions() as db:
            assert (await db.get(APIKey, issued.key_id)).app_id is None

        rebound = await test_client.put(f"{API_KEYS_PATH}{issued.key_id}", json={"app_id": app_id}, headers=admin_headers)
        assert rebound.status_code == 200, rebound.text
        restored = await test_client.get(f"/api/v1/agents/sessions/{thread_id}", headers=issued.headers)
        assert restored.status_code == 200 and restored.json()["id"] == thread_id
    finally:
        await delete_created_threads(thread_id)


@pytest.mark.parametrize("key_state", ["disabled", "expired", "revoked"])
async def test_personal_agents_key_lifecycle_blocks_auth_and_replay_does_not_restore(
    test_client, admin_headers, api_key_factory, postgres_sessions, key_state
):
    """本人受限 Key 的失效状态由持久事实执行，创建重放不能重新启用凭据。"""
    payload = {**_create_payload("Personal lifecycle"), "access_level": "agents"}
    issued = await api_key_factory(payload)
    catalog = await test_client.get("/api/v1/agents", headers=issued.headers)
    assert catalog.status_code == 200 and isinstance(catalog.json()["data"], list)
    if key_state == "revoked":
        changed = await test_client.delete(f"{API_KEYS_PATH}{issued.key_id}", headers=admin_headers)
    else:
        updates = {"is_enabled": False} if key_state == "disabled" else {"expires_at": (utc_now() - timedelta(days=1)).isoformat()}
        changed = await test_client.put(f"{API_KEYS_PATH}{issued.key_id}", json=updates, headers=admin_headers)
    assert changed.status_code == 200, changed.text

    replay = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert replay.status_code == (409 if key_state == "revoked" else 200), replay.text
    if key_state == "revoked":
        assert "secret" not in replay.json()
    else:
        assert replay.json()["secret"] == issued.response.json()["secret"]
    rejected = await test_client.get("/api/v1/agents", headers=issued.headers)
    assert rejected.status_code == 401, rejected.text
    async with postgres_sessions() as db:
        stored = await db.get(APIKey, issued.key_id)
        assert stored.app_id is None and stored.access_level == "agents"
        assert stored.is_valid() is False
        if key_state == "disabled":
            assert stored.is_enabled is False
        elif key_state == "expired":
            assert stored.expires_at < utc_now()
        else:
            assert stored.revoked_at is not None and stored.is_enabled is False
