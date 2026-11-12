"""WPS 命名 Tunnel 的配置校验、进程所有权与探针结果。"""

import asyncio

import pytest
from fastapi import HTTPException

from yuxi.services.wps_development_tunnel import WPSDevelopmentTunnel, WPS_TUNNEL_HOSTNAME


class FakeStdout:
    """提供可中断的 cloudflared 输出管道。"""

    def __init__(self):
        """初始化管道关闭信号。"""
        self.closed = asyncio.Event()

    async def read(self, size):
        """在假进程终止前维持管道打开。"""
        await self.closed.wait()
        return b""


class FakeProcess:
    """模拟由服务启动且可终止的 cloudflared 子进程。"""

    def __init__(self):
        """返回码为空表示隧道仍在运行。"""
        self.returncode = None
        self.stdout = FakeStdout()
        self.exited = asyncio.Event()
        self.terminated = False

    async def wait(self):
        """等待终止信号并返回退出码。"""
        await self.exited.wait()
        self.returncode = 0
        return self.returncode

    def terminate(self):
        """以可观察状态模拟发送终止信号。"""
        self.terminated = True
        self.stdout.closed.set()
        self.exited.set()

    def kill(self):
        """复用正常退出路径完成强制回收。"""
        self.terminate()


# 提供有效的命名 Tunnel 文件和环境配置。
def enable_compose_development(monkeypatch, tmp_path):
    """设置开发环境、固定域名、配置文件和凭据文件。"""
    config_file = tmp_path / "config.yml"
    credentials_file = tmp_path / "zs-dev.json"
    config_file.write_text("tunnel: placeholder\n", encoding="utf-8")
    credentials_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_CONFIG", str(config_file))
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_CREDENTIALS", str(credentials_file))


# 关闭、生产和非 Compose 环境不得暴露可启动隧道。
def test_tunnel_is_disabled_outside_compose_development(monkeypatch):
    """实际隧道只开放在 Docker Compose 开发拓扑。"""
    tunnel = WPSDevelopmentTunnel()
    for values in (
        {"YUXI_ENV": "production", "RUNNING_IN_DOCKER": "true"},
        {"YUXI_ENV": "development", "RUNNING_IN_DOCKER": "false"},
    ):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        assert tunnel.status()["enabled"] is False
        with pytest.raises(HTTPException, match="Docker Compose"):
            asyncio.run(tunnel.start())


# 缺少命名 Tunnel 文件时必须在启动前失败，不能伪装成随机联调地址。
@pytest.mark.parametrize("missing_file", ["config.yml", "zs-dev.json"])
def test_named_tunnel_requires_config_and_credentials(monkeypatch, tmp_path, missing_file):
    """空挂载目录返回可观察配置错误。"""
    enable_compose_development(monkeypatch, tmp_path)
    (tmp_path / missing_file).unlink()
    tunnel = WPSDevelopmentTunnel()
    assert tunnel.status()["enabled"] is False
    with pytest.raises(HTTPException, match="config"):
        asyncio.run(tunnel.start())


# 联调只使用产品约定的固定公网 HTTPS 域名，不接受请求或环境覆盖。
def test_named_tunnel_uses_fixed_public_https_hostname(monkeypatch, tmp_path):
    """命名隧道名称和域名始终保持 WPS 后台登记值。"""
    enable_compose_development(monkeypatch, tmp_path)
    tunnel = WPSDevelopmentTunnel()
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_PUBLIC_URL", "https://random.trycloudflare.com")
    monkeypatch.setenv("WPS_DEVELOPMENT_TUNNEL_NAME", "other-tunnel")
    assert tunnel.status()["enabled"] is True
    assert tunnel._configuration().name == "zs-dev"
    assert tunnel._configuration().hostname == "zs-dev.zs-wps.com"


# 只接受当前探针及固定域名来源，验证 OAuth 回调与隧道生命周期绑定。
def test_probe_and_callback_context_are_tied_to_current_tunnel(monkeypatch, tmp_path):
    """未知 nonce、错误来源和已停止隧道均不能取得回调。"""
    enable_compose_development(monkeypatch, tmp_path)
    tunnel = WPSDevelopmentTunnel()
    tunnel._state = "running"
    tunnel._public_url = f"https://{WPS_TUNNEL_HOSTNAME}"
    tunnel._nonce = "a-long-random-tunnel-nonce"
    assert tunnel.probe(tunnel._nonce) == {"nonce": tunnel._nonce}
    with pytest.raises(HTTPException) as error:
        tunnel.probe("another-random-tunnel-nonce")
    assert error.value.status_code == 404
    callback, tunnel_id = tunnel.authorization_context(f"https://{WPS_TUNNEL_HOSTNAME}")
    assert callback == f"https://{WPS_TUNNEL_HOSTNAME}/auth/wps/callback"
    assert tunnel.callback_is_active(callback, tunnel_id)
    assert tunnel.authorization_context("https://localhost:5173") == ("", "")
    tunnel._state, tunnel._public_url, tunnel._nonce = "stopped", "", ""
    assert not tunnel.callback_is_active(callback, tunnel_id)


# 模拟命名隧道进程和公网探测，验证固定目标、命令参数与关闭回收。
@pytest.mark.asyncio
async def test_start_publishes_only_verified_callback_and_stop_reaps_process(monkeypatch, tmp_path):
    """地址只来自固定产品配置，停止立即撤销当前授权身份。"""
    enable_compose_development(monkeypatch, tmp_path)
    tunnel = WPSDevelopmentTunnel()
    process = FakeProcess()
    commands = []
    probes = []

    async def spawn(*command, **options):
        """保留完整进程命令以断言隧道名称和配置路径不可由请求改变。"""
        commands.append(command)
        assert options["stdout"] == asyncio.subprocess.PIPE
        return process

    async def verify(url):
        """保留经过探测的固定 Cloudflare 地址。"""
        probes.append(url)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(tunnel, "_verify_public_site", verify)
    assert (await tunnel.start())["state"] == "starting"
    async with asyncio.timeout(1):
        while tunnel.status()["state"] == "starting":
            await asyncio.sleep(0)
    status = tunnel.status()
    assert status["running"]
    assert status["callback_url"] == "https://zs-dev.zs-wps.com/auth/wps/callback"
    assert probes == [f"https://{WPS_TUNNEL_HOSTNAME}"]
    assert commands[0] == (
        "/usr/local/bin/cloudflared", "tunnel", "--no-autoupdate", "--config",
        str(tmp_path / "config.yml"), "run", "--credentials-file", str(tmp_path / "zs-dev.json"), "zs-dev",
    )
    callback, tunnel_id = tunnel.authorization_context(f"https://{WPS_TUNNEL_HOSTNAME}")
    assert tunnel.callback_is_active(callback, tunnel_id)
    stopped = await tunnel.stop()
    assert stopped["state"] == "stopped" and stopped["callback_url"] == ""
    assert process.terminated and process.returncode == 0
    assert not tunnel.callback_is_active(callback, tunnel_id)


# 缺少镜像组件时必须返回可操作错误，不能宣称命名 Tunnel 已启动。
@pytest.mark.asyncio
async def test_missing_cloudflared_is_observable(monkeypatch, tmp_path):
    """cloudflared 不可执行时返回镜像重建提示。"""
    enable_compose_development(monkeypatch, tmp_path)
    tunnel = WPSDevelopmentTunnel()

    async def missing(*command, **options):
        """模拟镜像内没有 cloudflared 可执行文件。"""
        raise OSError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    status = await tunnel.start()
    assert status["state"] == "error"
    assert "重新构建 API 镜像" in status["message"]
