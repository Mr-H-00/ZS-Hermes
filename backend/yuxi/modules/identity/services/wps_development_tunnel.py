"""Compose 开发环境的 WPS 命名 Cloudflare Tunnel 入口。"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import httpx

from yuxi.shared.errors import ResourceNotFoundError, StateConflictError

WPS_CALLBACK_PATH = "/auth/wps/callback"
WPS_TUNNEL_PROBE_PATH = "/api/cloud-drive/wps/development-tunnel/probe"
WPS_TUNNEL_NAME = "zs-dev"
WPS_TUNNEL_HOSTNAME = "zs-dev.zs-wps.com"
TUNNEL_START_TIMEOUT = 60


@dataclass(frozen=True)
class NamedTunnelConfig:
    """保存启动命名 Tunnel 所需的非秘密配置和凭据文件位置。"""

    name: str
    hostname: str
    config_path: Path
    credentials_path: Path

    @property
    def public_url(self) -> str:
        """返回固定命名 Tunnel 的 HTTPS 公网地址。"""
        return f"https://{self.hostname}"


class WPSDevelopmentTunnel:
    """当前 Compose API 的单一命名 Tunnel，不创建或持久化 Cloudflare 凭据。"""

    def __init__(self) -> None:
        """初始化进程锁、生命周期状态和当前授权隔离标识。"""
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._state = "stopped"
        self._message = "启动后验证固定的命名 Tunnel HTTPS 地址"
        self._public_url = ""
        self._nonce = ""

    # 根据部署环境返回是否允许启动，不接受请求侧环境参数。
    @staticmethod
    def available() -> bool:
        """仅支持单进程 API 的 Docker Compose 开发拓扑。"""
        return os.getenv("YUXI_ENV") == "development" and os.getenv("RUNNING_IN_DOCKER") == "true"

    # 读取并校验命名 Tunnel 配置，不读取配置或凭据文件内容。
    @staticmethod
    def _configuration() -> NamedTunnelConfig | None:
        """读取固定名称和域名以及部署者提供的只读文件路径。"""
        config_path = os.getenv("WPS_DEVELOPMENT_TUNNEL_CONFIG", "/etc/cloudflared/config.yml").strip()
        credentials_path = os.getenv("WPS_DEVELOPMENT_TUNNEL_CREDENTIALS", "").strip()
        if not config_path or not credentials_path:
            return None
        config_file = Path(config_path).expanduser()
        credentials_file = Path(credentials_path).expanduser()
        if (
            not config_file.is_file()
            or not credentials_file.is_file()
            or not os.access(config_file, os.R_OK)
            or not os.access(credentials_file, os.R_OK)
        ):
            return None
        return NamedTunnelConfig(WPS_TUNNEL_NAME, WPS_TUNNEL_HOSTNAME, config_file, credentials_file)

    # 返回配置缺失时的统一提示，避免向前端暴露宿主机路径或凭据内容。
    @classmethod
    def _configuration_message(cls) -> str:
        """返回管理员可执行的命名 Tunnel 配置提示。"""
        if cls._configuration() is None:
            return (
                "请先将 zs-dev 命名 Tunnel 的 config.yml 和 UUID credentials JSON 放入 "
                "docker/cloudflared，并设置 WPS_DEVELOPMENT_TUNNEL_CREDENTIALS"
            )
        return ""

    # 回读进程与探针状态，返回独立于正式配置的地址投影。
    def status(self) -> dict:
        """进程退出或配置失效后立即撤销联调地址。"""
        if self._process and self._process.returncode is not None:
            self._state, self._public_url = "error", ""
            self._message = "联调隧道已退出，请重新启动"
        enabled = self.available()
        configuration = self._configuration() if enabled else None
        if not enabled:
            state, running, public_url = "disabled", False, ""
            message = "开发联调仅支持 Docker Compose 开发环境"
        elif configuration is None:
            state, running, public_url = "disabled", False, ""
            message = self._configuration_message()
        else:
            state = self._state
            running = state == "running"
            public_url = self._public_url if running else ""
            message = self._message
        return {
            "enabled": bool(enabled and configuration),
            "configured": bool(configuration),
            "state": state,
            "running": running,
            "public_url": public_url,
            "configured_public_url": configuration.public_url if configuration else "",
            "callback_url": f"{public_url}{WPS_CALLBACK_PATH}" if running else "",
            "message": message,
        }

    # 启动固定命名 Tunnel，返回 starting；后台探针成功后变为 running。
    async def start(self) -> dict:
        """管理员显式启动命名 Tunnel，重复启动复用同一进程。"""
        if not self.available():
            raise StateConflictError("开发联调仅支持 Docker Compose 开发环境")
        configuration = self._configuration()
        if configuration is None:
            raise StateConflictError(self._configuration_message())
        async with self._lock:
            if self._task and not self._task.done():
                return self.status()
            self._public_url = ""
            self._nonce = secrets.token_urlsafe(24)
            self._state, self._message = "starting", "正在启动命名 Tunnel 并检查公网连接…"
            try:
                self._process = await asyncio.create_subprocess_exec(
                    "/usr/local/bin/cloudflared",
                    "tunnel",
                    "--no-autoupdate",
                    "--config",
                    str(configuration.config_path),
                    "run",
                    "--credentials-file",
                    str(configuration.credentials_path),
                    configuration.name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except OSError:
                self._state, self._message = "error", "缺少联调组件，请重新构建 API 镜像后重试"
                self._nonce = ""
                return self.status()
            self._task = asyncio.create_task(self._run(self._process, configuration.public_url))
            return self.status()

    # 停止子进程并撤销地址，返回最终停止状态；同时作为 lifespan 清理入口。
    async def stop(self) -> dict:
        """API 重启和显式停止均回收命名 Tunnel 子进程。"""
        async with self._lock:
            self._state, self._public_url, self._nonce = "stopped", "", ""
            self._message = "开发联调已停止"
            task = self._task
            self._task = None
            process = self._process
            self._process = None
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if process:
                await self._terminate(process)
            return self.status()

    # 参数是随机探针 nonce；返回当前 API 实例的回声用于公网归属证明。
    def probe(self, nonce: str) -> dict:
        """只在启动或运行期间回应匹配的探针，不暴露配置和用户数据。"""
        if not self.available() or not self._nonce or not hmac.compare_digest(nonce, self._nonce):
            raise ResourceNotFoundError("联调探针不存在")
        return {"nonce": nonce}

    # 读取当前已探测地址，参数为页面来源；返回匹配来源的固定回调或空串。
    def authorization_context(self, origin: str | None) -> tuple[str, str]:
        """仅使用运行中的固定域名，不信任浏览器指定任意回调。"""
        state = self.status()
        if state["running"] and origin == state["public_url"]:
            return state["callback_url"], self._nonce
        return "", ""

    # 参数为持久化授权时的回调与隧道标识，返回该次联调是否仍活跃。
    def callback_is_active(self, callback: str, tunnel_id: str) -> bool:
        """停止和重新启动后即使域名相同也不能恢复旧授权。"""
        state = self.status()
        return bool(state["running"] and callback == state["callback_url"] and tunnel_id == self._nonce)

    # 参数是服务创建的进程和固定公网地址；验证通过后持续回收日志管道。
    async def _run(self, process: asyncio.subprocess.Process, public_url: str) -> None:
        """超时、探针失败和子进程退出均清除固定地址。"""
        drain = asyncio.create_task(self._drain_output(process))
        try:
            async with asyncio.timeout(TUNNEL_START_TIMEOUT):
                await self._verify_public_site(public_url)
                if process.returncode is not None:
                    raise RuntimeError("进程已退出")
                self._public_url = public_url
                self._state, self._message = "running", "命名 Tunnel 已就绪"
            await process.wait()
            self._public_url, self._nonce = "", ""
            self._state, self._message = "error", "联调隧道已退出，请重新启动"
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._state, self._message = "error", "固定域名公网探针超时，请检查 DNS、Tunnel 配置和外网连接"
        except Exception:
            self._state, self._message = "error", "联调连接失败，请检查固定域名和命名 Tunnel 配置后重新启动"
        finally:
            self._public_url, self._nonce = "", ""
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
            await self._terminate(process)
            if self._process is process:
                self._process = None

    # 持续消费子进程输出，参数为当前进程；不保存可能包含外部数据的日志。
    @staticmethod
    async def _drain_output(process: asyncio.subprocess.Process) -> None:
        """防止长期运行后输出缓冲区阻塞命名 Tunnel 进程。"""
        while await process.stdout.read(4096):
            pass

    # 参数为固定公网地址；验证 SPA 与当前 API 实例两段路径。
    async def _verify_public_site(self, public_url: str) -> None:
        """固定域名发布后重试传播，只有页面和随机回声均匹配才完成。"""
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            while True:
                if self._process and self._process.returncode is not None:
                    raise RuntimeError("进程已退出")
                try:
                    page = await client.get(f"{public_url}{WPS_CALLBACK_PATH}")
                    probe = await client.get(f"{public_url}{WPS_TUNNEL_PROBE_PATH}", params={"nonce": self._nonce})
                    if (
                        page.status_code == 200
                        and "text/html" in page.headers.get("content-type", "")
                        and '<div id="app">' in page.text
                        and probe.status_code == 200
                        and probe.json() == {"nonce": self._nonce}
                    ):
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(1)

    # 终止指定子进程，返回前确认其已退出，避免遗留公网入口。
    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """先发送正常退出信号，超时后强制回收命名 Tunnel。"""
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            await process.wait()


wps_development_tunnel = WPSDevelopmentTunnel()
