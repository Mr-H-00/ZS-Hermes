"""WPS 365 配置、本人 OAuth 授权和云文档用例。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import uuid
from datetime import timedelta
from email.utils import format_datetime
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.cloud_drive_repository import CloudDriveRepository
from yuxi.services.wps_development_tunnel import WPS_CALLBACK_PATH, wps_development_tunnel
from yuxi.storage.postgres.models_business import WPSCloudConfig, WPSConnection
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now, utc_now_naive

WPS_API_BASE = "https://openapi.wps.cn"
WPS_SCOPES = "kso.user_base.read kso.drive.readwrite kso.file.read kso.file.search"


# 校验待登记的回调字符串，返回规范化公网 HTTPS 地址，非法值抛出 ValueError。
def validate_wps_callback_url(value: str) -> str:
    """WPS 正式应用使用公开域名，拒绝本机、IP 和未加密回调。"""
    normalized = str(TypeAdapter(HttpUrl).validate_python(value))
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("WPS 回调地址必须使用公网 HTTPS 域名，不能使用 IP 地址")
    if parsed.scheme != "https" or "." not in host or host.endswith((".localhost", ".local")):
        raise ValueError("WPS 回调地址必须使用公网 HTTPS 域名，不能使用 localhost 或本机地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != WPS_CALLBACK_PATH:
        raise ValueError(f"回调地址必须以 {WPS_CALLBACK_PATH} 结尾且不含凭据、查询或片段")
    return normalized


class WPSConfigInput(BaseModel):
    """可修改的应用字段；空密钥保留旧值，清除必须显式选择。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool
    app_id: str = Field(max_length=255)
    app_secret: str = Field(default="", max_length=2048, repr=False)
    clear_secret: bool = False
    redirect_uri: str = Field(max_length=2048)

    # 校验回调 URL 字符串，返回完整规范地址，空值允许保存停用配置。
    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: str) -> str:
        """回调只能指向前端拥有的固定路径，不携带凭据或查询参数。"""
        if not value:
            return value
        return validate_wps_callback_url(value)


class WPSTokens(BaseModel):
    """只接受完整、未过期的 WPS OAuth 响应。"""

    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    expires_in: int = Field(gt=0, le=86400)
    refresh_expires_in: int = Field(gt=0, le=366 * 86400)


class WPSUser(BaseModel):
    """外部用户身份的最小可信投影。"""

    id: str = Field(min_length=1, max_length=255)
    user_name: str = Field(default="WPS 用户", max_length=255)
    company_id: str = Field(default="", max_length=255)


class CloudDriveService:
    """共享配置与按 UID 隔离的连接流程。"""

    # 用请求数据库会话构建服务；可注入 HTTP transport 以验证固定协议。
    def __init__(self, db: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None):
        """绑定事务及固定 WPS HTTP 客户端的传输层。"""
        self.db = db
        self.repo = CloudDriveRepository(db)
        self.transport = transport

    # 读取配置公开字段，密钥仅返回存在性。
    async def get_config(self) -> dict:
        """脱敏回读管理员共享配置。"""
        config = await self._config()
        return self._public_config(config)

    # 保存应用字段并提交，返回脱敏结果；参数是已校验配置模型。
    async def save_config(self, payload: WPSConfigInput) -> dict:
        """保存配置；任何有效字段改变都会使旧连接版本失效。"""
        config = await self._config(lock=True)
        if payload.clear_secret and payload.app_secret:
            raise HTTPException(400, "清除密钥和输入新密钥不能同时选择")
        # 重置密钥不依赖解密旧值，实例主密钥轮换后仍能重新配置。
        new_secret_encrypted = config.app_secret_encrypted
        if payload.clear_secret:
            new_secret_encrypted = ""
        elif payload.app_secret:
            new_secret_encrypted = self._encrypt(payload.app_secret)
        if payload.enabled and not (payload.app_id and new_secret_encrypted and (
            payload.redirect_uri or wps_development_tunnel.status()["enabled"]
        )):
            raise HTTPException(400, "启用 WPS 前请填写应用 ID、密钥和回调地址")
        changed = (config.enabled, config.app_id, config.app_secret_encrypted, config.redirect_uri) != (
            payload.enabled,
            payload.app_id,
            new_secret_encrypted,
            payload.redirect_uri,
        )
        config.enabled = payload.enabled
        config.app_id = payload.app_id
        config.redirect_uri = payload.redirect_uri
        config.app_secret_encrypted = new_secret_encrypted
        if changed:
            config.version = uuid.uuid4().hex
        await self.db.commit()
        return self._public_config(config)

    # 读取本人连接；recheck 为真时用 WPS 用户接口重新确认。
    async def status(self, uid: str, *, recheck: bool = False) -> dict:
        """返回本人连接状态；外部故障保留凭据但不投影为已连接。"""
        config = await self._config(lock=True, shared=True)
        connection = await self.repo.get_connection(uid, lock=recheck)
        status = self._connection_status(config, connection)
        if status["connected"] and recheck:
            try:
                token = await self._access_token(config, connection)
                user = await self._user(config, token)
                if user.id != connection.wps_user_id:
                    raise HTTPException(409, "WPS 身份已改变，请重新连接")
                connection.wps_user_name = user.user_name
                connection.company_id = user.company_id
                status = self._connection_status(config, connection)
            except HTTPException as exc:
                status = {**status, "connected": False, "state": "error", "message": str(exc.detail)}
            finally:
                # 身份检查失败也保存已轮换的 refresh_token，避免下次使用失效旧值。
                await self.db.commit()
        return self._with_tunnel_status(status)

    # 为 UID 建立一次性授权状态并提交，返回 WPS 授权跳转地址。
    async def authorize(self, uid: str, request_origin: str | None) -> dict:
        """授权状态持久化后才返回跳转链接。"""
        config = await self._config(lock=True, shared=True)
        callback_uri, tunnel_id = wps_development_tunnel.authorization_context(request_origin)
        callback_uri = callback_uri or config.redirect_uri
        self._require_enabled(config, callback_uri=callback_uri)
        callback = urlsplit(callback_uri)
        if request_origin != f"{callback.scheme}://{callback.netloc}":
            raise HTTPException(409, "请通过已登记的回调域名访问并登录 Yuxi，再连接 WPS")
        connection = await self.repo.ensure_connection(uid)
        state = secrets.token_urlsafe(32)
        connection.oauth_state_hash = hashlib.sha256(state.encode()).hexdigest()
        connection.oauth_expires_at = utc_now_naive() + timedelta(minutes=10)
        connection.oauth_config_version = config.version
        connection.oauth_redirect_uri = callback_uri
        connection.oauth_tunnel_id = tunnel_id or None
        await self.db.commit()
        query = urlencode(
            {
                "client_id": config.app_id,
                "response_type": "code",
                "redirect_uri": callback_uri,
                "scope": WPS_SCOPES,
                "state": state,
            }
        )
        return {"authorization_url": f"{WPS_API_BASE}/oauth2/auth?{query}"}

    # 校验本人 state，交换 code 并获取真实身份后提交连接。
    async def exchange(self, uid: str, code: str, state: str) -> dict:
        """一次性消费 OAuth state；配置变化或新授权不能被旧回调覆盖。"""
        config = await self._config(lock=True, shared=True)
        connection = await self.repo.get_connection(uid, lock=True)
        if not (
            connection
            and connection.oauth_state_hash
            and hmac.compare_digest(connection.oauth_state_hash, hashlib.sha256(state.encode()).hexdigest())
            and connection.oauth_expires_at
            and connection.oauth_expires_at > utc_now_naive()
            and connection.oauth_config_version == config.version
        ):
            raise HTTPException(400, "授权已失效或不属于当前用户，请重新连接 WPS")
        version = config.version
        expires_at = connection.oauth_expires_at
        redirect_uri = connection.oauth_redirect_uri or config.redirect_uri
        tunnel_id = connection.oauth_tunnel_id
        self._require_enabled(config, callback_uri=redirect_uri)
        if tunnel_id and not wps_development_tunnel.callback_is_active(redirect_uri, tunnel_id):
            raise HTTPException(409, "开发联调已停止或重启，请通过新地址重新连接 WPS")
        connection.oauth_state_hash = None
        # 消费事实先提交；外部交换失败时不能重放同一个 state。
        await self.db.commit()
        token_data = await self._token(
            config, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        )
        user = await self._user(config, token_data.access_token)
        config = await self._config(lock=True, shared=True)
        connection = await self.repo.get_connection(uid, lock=True)
        if not (
            config.enabled
            and config.version == version
            and connection
            and connection.oauth_state_hash is None
            and connection.oauth_config_version == version
            and connection.oauth_expires_at == expires_at
            and connection.oauth_redirect_uri == redirect_uri
            and connection.oauth_tunnel_id == tunnel_id
            and (not tunnel_id or wps_development_tunnel.callback_is_active(redirect_uri, tunnel_id))
        ):
            raise HTTPException(409, "配置或授权已更新，请重新连接 WPS")
        self._store_tokens(connection, token_data)
        connection.wps_user_id = user.id
        connection.wps_user_name = user.user_name
        connection.company_id = user.company_id
        connection.config_version = version
        connection.connected_at = utc_now_naive()
        connection.oauth_expires_at = None
        connection.oauth_config_version = None
        connection.oauth_redirect_uri = None
        connection.oauth_tunnel_id = None
        await self.db.commit()
        return self._with_tunnel_status(self._connection_status(config, connection))

    # 清除 UID 的凭据和待授权状态，保留空连接行用于并发锁。
    async def disconnect(self, uid: str) -> dict:
        """删除本人本地 WPS 凭据。"""
        config = await self._config(lock=True, shared=True)
        connection = await self.repo.get_connection(uid, lock=True)
        if connection:
            for name in (
                "access_token_encrypted",
                "refresh_token_encrypted",
                "expires_at",
                "refresh_expires_at",
                "config_version",
                "wps_user_id",
                "wps_user_name",
                "company_id",
                "connected_at",
                "oauth_state_hash",
                "oauth_expires_at",
                "oauth_config_version",
                "oauth_redirect_uri",
                "oauth_tunnel_id",
            ):
                setattr(connection, name, None)
            await self.db.commit()
        return self._with_tunnel_status(self._connection_status(config, connection))

    # 使用 UID 的有效令牌请求固定文件接口，返回协议 data。
    async def files_request(self, uid: str, path: str, params: dict | None = None) -> dict:
        """WPS 自身执行文件授权，本地仅提供当前用户的令牌。"""
        config = await self._config(lock=True, shared=True)
        self._require_enabled(config, require_callback=False)
        connection = await self.repo.get_connection(uid, lock=True)
        if not self._connection_status(config, connection)["connected"]:
            raise HTTPException(409, "请先连接本人 WPS 云文档")
        token = await self._access_token(config, connection)
        try:
            data = await self._api(config, token, path, params)
        finally:
            # 即使文件接口失败，也保留已经轮换的凭据。
            await self.db.commit()
        return data

    # 读取配置行，缺少迁移时明确失败。
    async def _config(self, *, lock: bool = False, shared: bool = False) -> WPSCloudConfig:
        """配置表必须由唯一 migrator 初始化。"""
        config = await self.repo.get_config(lock=lock, shared=shared)
        if config is None:
            raise HTTPException(503, "WPS 配置尚未初始化，请完成存储迁移")
        return config

    # 从配置记录构建公开值，返回是否已有应用密钥。
    @staticmethod
    def _public_config(config: WPSCloudConfig) -> dict:
        """密钥不进入任何配置响应。"""
        return {
            "enabled": config.enabled,
            "app_id": config.app_id,
            "redirect_uri": config.redirect_uri,
            "secret_configured": bool(config.app_secret_encrypted),
        }

    # 基于配置版本与令牌有效期计算状态，返回用户可读投影。
    @staticmethod
    def _connection_status(config: WPSCloudConfig, connection: WPSConnection | None) -> dict:
        """保存配置不代表本人已授权。"""
        authorization_origin = None
        if config.redirect_uri:
            try:
                callback = urlsplit(validate_wps_callback_url(config.redirect_uri))
                authorization_origin = f"{callback.scheme}://{callback.netloc}"
            except ValueError:
                pass
        configured = bool(config.app_id and config.app_secret_encrypted and (
            authorization_origin or wps_development_tunnel.status()["running"]
        ))
        state, message = "disconnected", "首次连接 WPS 后，可使用本人云文档"
        if not config.enabled:
            state, message = "disabled", "WPS 云文档未启用，请联系管理员"
        elif config.redirect_uri and not authorization_origin:
            configured = False
            state, message = "invalid_callback", "回调地址需使用公网 HTTPS 域名，请管理员重新保存配置"
        elif not configured:
            state, message = "unconfigured", "WPS 应用配置不完整，请联系管理员"
        elif connection and connection.access_token_encrypted:
            if connection.config_version != config.version:
                state, message = "reconnect", "应用配置已更新，请重新连接 WPS"
            elif (connection.expires_at and connection.expires_at > utc_now_naive()) or (
                connection.refresh_expires_at and connection.refresh_expires_at > utc_now_naive()
            ):
                state, message = "connected", "已连接本人 WPS 云文档"
            else:
                state, message = "reconnect", "WPS 授权已过期，请重新连接"
        return {
            "enabled": config.enabled,
            "configured": configured,
            "authorization_origin": authorization_origin,
            "connected": state == "connected",
            "state": state,
            "message": message,
            "user": (
                {"name": connection.wps_user_name, "id": connection.wps_user_id}
                if connection and state == "connected"
                else None
            ),
            "connected_at": format_utc_datetime(connection.connected_at) if connection else None,
        }

    # 给连接状态附加命名隧道的公开投影，正式回调字段保持独立。
    @staticmethod
    def _with_tunnel_status(status: dict) -> dict:
        """让设置页和工作台能判断当前浏览器是否位于联调域名。"""
        tunnel = wps_development_tunnel.status()
        return {
            **status,
            "development_tunnel_enabled": tunnel["enabled"],
            "development_tunnel_active": tunnel["running"],
            "development_tunnel_state": tunnel["state"],
            "development_callback_url": tunnel["callback_url"],
            "development_callback_origin": tunnel["public_url"],
            "development_configured_origin": tunnel["configured_public_url"],
            "development_tunnel_message": tunnel["message"],
        }

    # 对共享配置检查启用及完整性，失败阻止外部请求。
    @staticmethod
    def _require_enabled(
        config: WPSCloudConfig,
        *,
        callback_uri: str | None = None,
        require_callback: bool = True,
    ) -> None:
        """未启用或未配置的 WPS 明确拒绝连接。"""
        callback = config.redirect_uri if callback_uri is None else callback_uri
        if not config.enabled or not (config.app_id and config.app_secret_encrypted):
            raise HTTPException(409, "请先启用并保存完整的 WPS 应用配置")
        if require_callback and not callback:
            raise HTTPException(409, "请先启动开发联调或保存公网 HTTPS 回调地址")
        try:
            if callback:
                validate_wps_callback_url(callback)
        except ValueError:
            raise HTTPException(409, "WPS 回调需使用公网 HTTPS 域名，请管理员重新保存配置") from None

    # 读取或串行刷新本人令牌，返回仅用于后端外部请求的明文。
    async def _access_token(self, config: WPSCloudConfig, connection: WPSConnection) -> str:
        """持有连接行锁，防止并发刷新覆盖轮换后的令牌。"""
        if connection.expires_at and connection.expires_at > utc_now_naive() + timedelta(seconds=30):
            return self._decrypt(connection.access_token_encrypted)
        if not (
            connection.refresh_token_encrypted
            and connection.refresh_expires_at
            and connection.refresh_expires_at > utc_now_naive()
        ):
            raise HTTPException(409, "WPS 授权已过期，请重新连接")
        tokens = await self._token(
            config, {"grant_type": "refresh_token", "refresh_token": self._decrypt(connection.refresh_token_encrypted)}
        )
        self._store_tokens(connection, tokens)
        # 轮换结果先持久化，后续文件接口失败不会回滚已使用的刷新凭据。
        await self.db.flush()
        return tokens.access_token

    # 解析外部 OAuth token，返回严格模型并避免泄漏响应或凭据。
    async def _token(self, config: WPSCloudConfig, fields: dict) -> WPSTokens:
        """按 WPS 官方表单协议交换或刷新令牌。"""
        request = httpx.Request(
            "POST",
            f"{WPS_API_BASE}/oauth2/token",
            data={**fields, "client_id": config.app_id, "client_secret": self._decrypt(config.app_secret_encrypted)},
        )
        data = await self._send(request)
        try:
            return WPSTokens.model_validate(data)
        except ValueError:
            raise HTTPException(502, "WPS 授权响应不完整，请重新连接") from None

    # 从当前用户接口读取身份，返回校验后的 WPS 用户模型。
    async def _user(self, config: WPSCloudConfig, token: str) -> WPSUser:
        """身份响应不完整时明确失败，外部数据不进入错误响应。"""
        data = await self._api(config, token, "/v7/users/current")
        try:
            return WPSUser.model_validate(data)
        except ValueError:
            raise HTTPException(502, "WPS 用户身份响应不完整，请重新连接") from None

    # 构造固定主机的签名请求，返回云文档 data 对象。
    async def _api(self, config: WPSCloudConfig, token: str, path: str, params: dict | None = None) -> dict:
        """签名使用最终编码后的 URI，与实际发送请求保持一致。"""
        request = httpx.Request(
            "GET",
            f"{WPS_API_BASE}{path}",
            params=params,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        request.headers.update(
            kso_signature(
                config.app_id,
                self._decrypt(config.app_secret_encrypted),
                request.method,
                request.url.raw_path.decode(),
                request.headers["Content-Type"],
                format_datetime(utc_now(), usegmt=True),
                request.content,
            )
        )
        payload = await self._send(request)
        if not isinstance(payload.get("data"), dict):
            raise HTTPException(502, "WPS 返回了无效的云文档数据")
        return payload["data"]

    # 发送固定 WPS 请求，返回成功 JSON；外部消息不得直接回显。
    async def _send(self, request: httpx.Request) -> dict:
        """对外部协议响应执行校验，令牌失败使用 409 避免登出 Yuxi。"""
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
                response = await client.send(request)
            if response.status_code == 401:
                raise HTTPException(409, "WPS 授权已失效，请重新连接")
            if response.status_code == 403:
                raise HTTPException(403, "WPS 应用或本人没有该云文档权限")
            if not response.is_success:
                raise HTTPException(502, "WPS 服务请求失败，请重新检查连接和应用权限")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "无法读取 WPS 服务响应，请稍后重新检查") from None
        if not isinstance(payload, dict) or payload.get("code", 0) != 0:
            raise HTTPException(502, "WPS 拒绝了请求，请检查应用权限或重新连接")
        return payload

    # 将严格 token 模型写入用户连接，密文绑定当前实例领域密钥。
    def _store_tokens(self, connection: WPSConnection, tokens: WPSTokens) -> None:
        """持久化凭据密文与外部协议声明的有效期。"""
        now = utc_now_naive()
        connection.access_token_encrypted = self._encrypt(tokens.access_token)
        connection.refresh_token_encrypted = self._encrypt(tokens.refresh_token)
        connection.expires_at = now + timedelta(seconds=tokens.expires_in)
        connection.refresh_expires_at = now + timedelta(seconds=tokens.refresh_expires_in)

    # 加密非空凭据文本并返回 Fernet 密文。
    @staticmethod
    def _encrypt(value: str) -> str:
        """使用现有持久主密钥派生 WPS 专用加密密钥。"""
        key = base64.urlsafe_b64encode(AuthUtils.derive_encryption_key("wps-cloud-drive"))
        return Fernet(key).encrypt(value.encode()).decode()

    # 解密密文凭据，错误密钥或损坏密文明确要求重连。
    @staticmethod
    def _decrypt(value: str) -> str:
        """密钥轮换后不静默接受无法解密的历史凭据。"""
        key = base64.urlsafe_b64encode(AuthUtils.derive_encryption_key("wps-cloud-drive"))
        try:
            return Fernet(key).decrypt(value.encode()).decode()
        except InvalidToken:
            raise HTTPException(409, "WPS 凭据无法解密，请重新保存密钥并连接") from None


# 用 APPID、APPKEY、HTTP 方法、最终 URI、类型、日期和请求体生成官方 KSO-1 请求头。
def kso_signature(app_id: str, secret: str, method: str, uri: str, content_type: str, date: str, body: bytes) -> dict:
    """实现 WPS 官方 KSO-1；空请求体的摘要为空字符串。"""
    digest = hashlib.sha256(body).hexdigest() if body else ""
    message = f"KSO-1{method}{uri}{content_type}{date}{digest}".encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {"X-Kso-Date": date, "X-Kso-Authorization": f"KSO-1 {app_id}:{signature}"}


# 编码外部文件 ID 字符串，返回安全的路径片段。
def wps_id_segment(value: str) -> str:
    """防止外部 ID 改写固定 API 路径。"""
    if value in {".", ".."}:
        raise HTTPException(400, "无效的 WPS 文件标识")
    return quote(value, safe="")
