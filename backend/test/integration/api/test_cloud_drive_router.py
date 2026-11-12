"""真实 HTTP、PostgreSQL 与确定性 WPS 协议回放的连接证据。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.cloud_drive_router import cloud_drive
from server.utils.auth_middleware import get_db
from yuxi.services.cloud_drive_service import CloudDriveService
from yuxi.services.wps_development_tunnel import wps_development_tunnel
from yuxi.storage.postgres.models_business import Base, Department, User, WPSCloudConfig, WPSConnection
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
BASE_URL = "/api/cloud-drive/wps"
CONFIG = {
    "enabled": True,
    "app_id": "example-app",
    "app_secret": "example-app-secret",
    "redirect_uri": "https://example.com/auth/wps/callback",
}


# 隔离 Schema 测试不访问共享 API，不需要其启动预检。
@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """使用本文件的独立 HTTP 实例。"""


# 此测试没有创建共享知识资源，无需执行全局清理。
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """知识资源清理由其 owning 测试执行。"""
    yield


# 此测试没有沙盒副作用，无需执行全局清理。
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """不触碰其他测试的 Sandbox。"""
    yield


class WPSReplay:
    """只回放 WPS wire，Yuxi 的路由、认证和持久化使用真实实现。"""

    # 初始化协议回放状态，参数为空，结果在 handler 中观察。
    def __init__(self):
        """保存请求与可控失败，用于隔离、重放和刷新断言。"""
        self.requests = []
        self.user_error = False
        self.token_error = False
        self.invalid_user = False
        self.files_error = False
        self.token_started = asyncio.Event()
        self.token_release = None

    # 接收真实 httpx 请求，返回固定 WPS JSON 响应。
    async def handle(self, request: httpx.Request) -> httpx.Response:
        """断言实际发送的表单、Bearer 与编码 URI。"""
        self.requests.append(request)
        assert request.url.host == "openapi.wps.cn"
        if request.url.path == "/oauth2/token":
            fields = parse_qs(request.content.decode())
            assert fields["client_id"] == ["example-app"]
            assert fields["client_secret"] == ["example-app-secret"]
            self.token_started.set()
            if self.token_release:
                await self.token_release.wait()
            if self.token_error:
                return httpx.Response(400, json={"error": "invalid_grant"})
            identity = fields.get("code", ["rotated"])[0]
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{identity}",
                    "refresh_token": f"refresh-{identity}",
                    "expires_in": 3600,
                    "refresh_expires_in": 86400,
                },
            )
        assert request.headers["X-Kso-Authorization"].startswith("KSO-1 example-app:")
        if request.url.path == "/v7/users/current":
            if self.user_error:
                return httpx.Response(503, json={})
            if self.invalid_user:
                return httpx.Response(200, json={"code": 0, "data": {"id": None}})
            identity = request.headers["Authorization"].removeprefix("Bearer access-")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"id": identity, "user_name": f"WPS {identity}", "company_id": "example-company"},
                },
            )
        if self.files_error:
            return httpx.Response(503, json={})
        if request.url.path == "/v7/drives":
            assert request.url.params["allotee_type"] == "user"
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"id": "drive-a", "name": "本人文档库"}]}})
        if request.url.path == "/v7/files/search":
            assert request.url.params["type"] == "file_name"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"file": {"id": "file-a", "name": "example.docx", "drive_id": "drive-a", "type": "file"}}
                        ]
                    },
                },
            )
        if request.url.path.endswith("/children"):
            assert request.url.params["with_link"] == "true"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "id": "file-a",
                                "name": "example.docx",
                                "type": "file",
                                "link_url": "https://example.com/document",
                            }
                        ]
                    },
                },
            )
        assert request.url.path.endswith("/download")
        return httpx.Response(200, json={"code": 0, "data": {"url": "https://example.com/download"}})


# fixture 创建独立 PG Schema 和监听真实 TCP 的 FastAPI 实例，返回客户端与回读句柄。
@pytest_asyncio.fixture
async def cloud_env(monkeypatch, tmp_path):
    """测试结束销毁自身 Schema，不改动共享 WPS 配置和用户。"""
    schema = f"pytest_wps_{uuid.uuid4().hex[:16]}"
    config_file = tmp_path / "config.yml"
    credentials_file = tmp_path / "zs-dev.json"
    config_file.write_text("tunnel: placeholder\n", encoding="utf-8")
    credentials_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_CONFIG", str(config_file))
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_CREDENTIALS", str(credentials_file))
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[Department.__table__, User.__table__, WPSCloudConfig.__table__, WPSConnection.__table__]
            )
        )
    async with factory() as db:
        department = Department(name="WPS test department")
        db.add(department)
        await db.flush()
        users = [
            User(
                uid=name,
                username=name,
                password_hash="test-placeholder",
                department_id=department.id,
                role="admin" if name == "admin" else "user",
            )
            for name in ("admin", "alice", "bob")
        ]
        db.add_all([*users, WPSCloudConfig(id=1)])
        await db.commit()
        headers = {
            user.uid: {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
            for user in users
        }

    # 使用真实数据库依赖；只隔离数据库位置，认证依赖保持生产实现。
    async def isolated_db():
        """向请求提供独立 Schema 的会话并在退出时回滚未提交状态。"""
        async with factory() as db:
            yield db

    replay = WPSReplay()
    original_init = CloudDriveService.__init__

    # 将外部传输绑定到确定性协议响应，参数与生产构造器一致。
    def replay_init(self, db, **kwargs):
        """替换外部 WPS wire，不替换业务或 repository。"""
        original_init(self, db, transport=httpx.MockTransport(replay.handle))

    monkeypatch.setattr(CloudDriveService, "__init__", replay_init)
    app = FastAPI()
    app.include_router(cloud_drive, prefix="/api")
    app.dependency_overrides[get_db] = isolated_db
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical", lifespan="off")
    listener = config.bind_socket()
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=10) as client:
            yield {"client": client, "db": factory, "headers": headers, "replay": replay}
    finally:
        if replay.token_release:
            replay.token_release.set()
        server.should_exit = True
        await task
        listener.close()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


# 保存固定应用配置，返回经 HTTP 回读的公开字段。
async def configure(env):
    """应用配置成功后用户仍未授权。"""
    response = await env["client"].put(f"{BASE_URL}/config", headers=env["headers"]["admin"], json=CONFIG)
    assert response.status_code == 200
    assert response.json()["secret_configured"] is True
    return response.json()


# 为指定测试用户发起授权，返回 URL 中的一次性 state。
async def authorize(env, uid="alice"):
    """state 只保存在 URL 和测试内存，数据库保存哈希。"""
    response = await env["client"].post(
        f"{BASE_URL}/authorize", headers={**env["headers"][uid], "Origin": "https://example.com"}
    )
    assert response.status_code == 200
    url = httpx.URL(response.json()["authorization_url"])
    assert url.host == "openapi.wps.cn"
    assert url.params["redirect_uri"] == CONFIG["redirect_uri"]
    return url.params["state"]


# 从非法来源或历史本机配置发起授权，回读数据库确认不产生 state。
async def test_authorization_rejects_local_callback_and_different_browser_origin(cloud_env):
    """公网回调与浏览器来源必须一致，旧本机配置也不能继续授权。"""
    env = cloud_env
    await configure(env)
    for origin in (None, "http://localhost:5173", "https://other.example.com", "https://example.com:8443"):
        headers = dict(env["headers"]["alice"])
        if origin:
            headers["Origin"] = origin
        response = await env["client"].post(f"{BASE_URL}/authorize", headers=headers)
        assert response.status_code == 409
        assert "已登记" in response.json()["detail"]
    invalid_save = await env["client"].put(
        f"{BASE_URL}/config", headers=env["headers"]["admin"],
        json={**CONFIG, "redirect_uri": "http://localhost:5173/auth/wps/callback"},
    )
    assert invalid_save.status_code == 422
    async with env["db"]() as db:
        assert await db.get(WPSConnection, "alice") is None
        config = await db.get(WPSCloudConfig, 1)
        assert config.redirect_uri == CONFIG["redirect_uri"]
        config.redirect_uri = "http://localhost:5173/auth/wps/callback"
        await db.commit()
    status = (await env["client"].get(f"{BASE_URL}/connection", headers=env["headers"]["alice"])).json()
    assert status["state"] == "invalid_callback" and status["configured"] is False
    assert status["authorization_origin"] is None
    response = await env["client"].post(
        f"{BASE_URL}/authorize", headers={**env["headers"]["alice"], "Origin": "http://localhost:5173"}
    )
    assert response.status_code == 409 and "公网 HTTPS" in response.json()["detail"]
    async with env["db"]() as db:
        assert await db.get(WPSConnection, "alice") is None
    assert env["replay"].requests == []


# 临时隧道的回调独立于正式配置，停止后旧 state 不能继续交换令牌。
async def test_development_tunnel_callback_is_separate_and_stale_state_fails(cloud_env, monkeypatch):
    """联调域名只在当前隧道生命周期内有效，正式回调字段保持不变。"""
    env = cloud_env
    await configure(env)
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
    original = (wps_development_tunnel._state, wps_development_tunnel._public_url, wps_development_tunnel._nonce)
    wps_development_tunnel._state = "running"
    wps_development_tunnel._public_url = "https://zs-dev.zs-wps.com"
    wps_development_tunnel._nonce = "development-tunnel-test-nonce"
    try:
        response = await env["client"].post(
            f"{BASE_URL}/authorize",
            headers={**env["headers"]["alice"], "Origin": "https://zs-dev.zs-wps.com"},
        )
        assert response.status_code == 200
        url = httpx.URL(response.json()["authorization_url"])
        assert url.params["redirect_uri"] == "https://zs-dev.zs-wps.com/auth/wps/callback"
        state = url.params["state"]
        async with env["db"]() as db:
            row = await db.get(WPSConnection, "alice")
            assert row.oauth_redirect_uri == url.params["redirect_uri"]
            assert row.oauth_tunnel_id == "development-tunnel-test-nonce"
            config = await db.get(WPSCloudConfig, 1)
            assert config.redirect_uri == CONFIG["redirect_uri"]
        wps_development_tunnel._state = "stopped"
        wps_development_tunnel._public_url = ""
        wps_development_tunnel._nonce = ""
        response = await env["client"].post(
            f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": state}
        )
        assert response.status_code == 409
        assert env["replay"].requests == []
    finally:
        wps_development_tunnel._state, wps_development_tunnel._public_url, wps_development_tunnel._nonce = original


# 指定 UID 完成本人的确定性授权，返回最终连接投影。
async def connect(env, uid="alice"):
    """通过 HTTP 交换和数据库提交确认连接成功。"""
    state = await authorize(env, uid)
    response = await env["client"].post(
        f"{BASE_URL}/exchange", headers=env["headers"][uid], json={"code": uid, "state": state}
    )
    assert response.status_code == 200
    assert response.json()["connected"] is True
    return response.json()


# 在真实 HTTP 中验证配置权限和密钥的保留、清除与重置。
async def test_config_permissions_round_trip_and_secret_recovery(cloud_env):
    """配置始终脱敏，非管理员写入失败，损坏密文仍能替换。"""
    env = cloud_env
    client, headers = env["client"], env["headers"]
    assert (await client.get(f"{BASE_URL}/connection")).status_code == 401
    assert (await client.get(f"{BASE_URL}/config", headers=headers["alice"])).status_code == 403
    assert (await client.put(f"{BASE_URL}/config", headers=headers["alice"], json=CONFIG)).status_code == 403
    await configure(env)
    async with env["db"]() as db:
        config = await db.get(WPSCloudConfig, 1)
        version, cipher = config.version, config.app_secret_encrypted
        assert cipher != CONFIG["app_secret"]
        assert CloudDriveService._decrypt(cipher) == CONFIG["app_secret"]
    response = await client.get(f"{BASE_URL}/config", headers=headers["admin"])
    assert set(response.json()) == {"enabled", "app_id", "redirect_uri", "secret_configured"}
    assert CONFIG["app_secret"] not in response.text
    status = (await client.get(f"{BASE_URL}/connection", headers=headers["alice"])).json()
    assert status["configured"] and not status["connected"]
    response = await client.put(f"{BASE_URL}/config", headers=headers["admin"], json={**CONFIG, "app_secret": ""})
    assert response.status_code == 200
    async with env["db"]() as db:
        config = await db.get(WPSCloudConfig, 1)
        assert (config.version, config.app_secret_encrypted) == (version, cipher)
        config.app_secret_encrypted = "damaged-cipher"
        await db.commit()
    await configure(env)
    conflicting = await client.put(
        f"{BASE_URL}/config", headers=headers["admin"], json={**CONFIG, "clear_secret": True}
    )
    assert conflicting.status_code == 400
    incomplete = await client.put(
        f"{BASE_URL}/config", headers=headers["admin"], json={**CONFIG, "app_secret": "", "clear_secret": True}
    )
    assert incomplete.status_code == 400
    response = await client.put(
        f"{BASE_URL}/config",
        headers=headers["admin"],
        json={**CONFIG, "enabled": False, "app_secret": "", "clear_secret": True},
    )
    assert response.json()["secret_configured"] is False
    async with env["db"]() as db:
        assert (await db.get(WPSCloudConfig, 1)).app_secret_encrypted == ""


# 验证跨用户、过期、错误、重放和新授权覆盖旧 state 的失败原因。
async def test_oauth_state_is_user_bound_one_time_and_expiring(cloud_env):
    """非法 state 不能交换外部 code，也不能形成连接。"""
    env = cloud_env
    await configure(env)
    state = await authorize(env)
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        assert row.oauth_state_hash == hashlib.sha256(state.encode()).hexdigest()
        assert row.access_token_encrypted is None
    client = env["client"]
    for uid, invalid_state in (("bob", state), ("alice", "x" * 32)):
        response = await client.post(
            f"{BASE_URL}/exchange", headers=env["headers"][uid], json={"code": "alice", "state": invalid_state}
        )
        assert response.status_code == 400
    assert env["replay"].requests == []
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        row.oauth_expires_at = utc_now_naive() - timedelta(seconds=1)
        await db.commit()
    response = await client.post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": state}
    )
    assert response.status_code == 400
    old_state = await authorize(env)
    new_state = await authorize(env)
    response = await client.post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": old_state}
    )
    assert response.status_code == 400
    response = await client.post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": new_state}
    )
    assert response.json()["user"]["id"] == "alice"
    replay = await client.post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": new_state}
    )
    assert replay.status_code == 400
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        assert row.oauth_state_hash is None and row.oauth_expires_at is None
        assert CloudDriveService._decrypt(row.access_token_encrypted) == "access-alice"


# 验证交换失败会消耗 state，外部身份校验失败不写连接。
@pytest.mark.parametrize("failure", ["token_error", "invalid_user"])
async def test_failed_exchange_never_publishes_connection_or_allows_replay(cloud_env, failure):
    """外部错误后用户必须重新发起授权。"""
    env = cloud_env
    await configure(env)
    state = await authorize(env)
    setattr(env["replay"], failure, True)
    response = await env["client"].post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": state}
    )
    assert response.status_code == 502
    response = await env["client"].post(
        f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": state}
    )
    assert response.status_code == 400
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        assert row.access_token_encrypted is None and row.oauth_state_hash is None


# 验证授权交换期间断开连接会使旧回调提交失败。
async def test_disconnect_during_exchange_cannot_restore_connection(cloud_env):
    """断开后的旧回调不能重新发布凭据。"""
    env = cloud_env
    await configure(env)
    state = await authorize(env)
    env["replay"].token_release = asyncio.Event()
    pending = asyncio.create_task(
        env["client"].post(
            f"{BASE_URL}/exchange", headers=env["headers"]["alice"], json={"code": "alice", "state": state}
        )
    )
    await asyncio.wait_for(env["replay"].token_started.wait(), 5)
    response = await env["client"].delete(f"{BASE_URL}/connection", headers=env["headers"]["alice"])
    assert response.json()["connected"] is False
    env["replay"].token_release.set()
    assert (await pending).status_code == 409
    async with env["db"]() as db:
        assert (await db.get(WPSConnection, "alice")).access_token_encrypted is None


# 验证文件请求使用各自令牌，配置更新和断开阻止读取。
async def test_files_are_user_isolated_and_config_changes_require_reconnect(cloud_env):
    """文件列表、搜索与下载经过当前用户授权，并回读最终凭据隔离。"""
    env = cloud_env
    await configure(env)
    assert (await env["client"].get(f"{BASE_URL}/drives", headers=env["headers"]["alice"])).status_code == 409
    assert env["replay"].requests == []
    for uid in ("alice", "bob"):
        await connect(env, uid)
        response = await env["client"].get(f"{BASE_URL}/drives", headers=env["headers"][uid])
        assert response.json()["items"][0]["name"] == "本人文档库"
        assert env["replay"].requests[-1].headers["Authorization"] == f"Bearer access-{uid}"
    for path, params in (
        ("files", {"drive_id": "drive-a", "parent_id": "0"}),
        ("search", {"keyword": "example"}),
        ("download", {"drive_id": "drive-a", "file_id": "file-a"}),
    ):
        response = await env["client"].get(f"{BASE_URL}/{path}", params=params, headers=env["headers"]["alice"])
        assert response.status_code == 200
        assert env["replay"].requests[-1].headers["Authorization"] == "Bearer access-alice"
    await env["client"].delete(f"{BASE_URL}/connection", headers=env["headers"]["alice"])
    async with env["db"]() as db:
        assert (await db.get(WPSConnection, "alice")).access_token_encrypted is None
        assert (await db.get(WPSConnection, "bob")).access_token_encrypted is not None
    await env["client"].put(
        f"{BASE_URL}/config",
        headers=env["headers"]["admin"],
        json={**CONFIG, "app_id": "changed-app", "app_secret": ""},
    )
    status = (await env["client"].get(f"{BASE_URL}/connection", headers=env["headers"]["bob"])).json()
    assert status["state"] == "reconnect" and status["user"] is None
    count = len(env["replay"].requests)
    assert (await env["client"].get(f"{BASE_URL}/drives", headers=env["headers"]["bob"])).status_code == 409
    assert len(env["replay"].requests) == count


# 过期令牌并制造身份或文件错误，验证已轮换凭据仍提交数据库。
@pytest.mark.parametrize("endpoint", ["connection/check", "drives"])
async def test_refreshed_tokens_survive_following_provider_failure(cloud_env, endpoint):
    """后续请求失败不回滚已经使用的 refresh_token。"""
    env = cloud_env
    await configure(env)
    await connect(env)
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        row.expires_at = utc_now_naive() - timedelta(seconds=1)
        await db.commit()
    env["replay"].user_error = True
    env["replay"].files_error = True
    response = await env["client"].request(
        "POST" if endpoint.endswith("check") else "GET", f"{BASE_URL}/{endpoint}", headers=env["headers"]["alice"]
    )
    if endpoint.endswith("check"):
        assert response.json()["state"] == "error" and response.json()["connected"] is False
    else:
        assert response.status_code == 502
    async with env["db"]() as db:
        row = await db.get(WPSConnection, "alice")
        assert CloudDriveService._decrypt(row.refresh_token_encrypted) == "refresh-rotated"
        assert row.expires_at > utc_now_naive()
