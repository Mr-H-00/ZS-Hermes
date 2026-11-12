"""runner 公共健康错误经过真实 TCP HTTP 验证。"""

from types import SimpleNamespace

import pytest

from test.integration.support.module_audit import serve_http
from yuxi.sandbox_runner import main as runner

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_runner_health_failure_keeps_internal_diagnostics_in_logs(monkeypatch, caplog):
    """不可用正文保持稳定，底层人工诊断只进入日志。"""
    private_detail = "FIXTURE_ONLY /private/example-root internal.example.test"

    def failing_health():
        """注入健康检查的底层失败，不访问实际 Docker。"""
        raise RuntimeError(private_detail)

    monkeypatch.setattr(runner, "get_manager", lambda: SimpleNamespace(health=failing_health))
    async with serve_http(runner.app) as client:
        response = await client.get("/healthz")
    assert response.status_code == 503
    assert private_detail not in response.text
    assert response.json() == {"ok": False, "error": "sandbox_unavailable"}
    assert private_detail in caplog.text


async def test_runner_health_success_preserves_readiness_payload(monkeypatch):
    """健康状态仍来自真正的 manager 结果，不能把不可用伪装成功。"""
    payload = {"ok": True, "backend": "container"}
    monkeypatch.setattr(runner, "get_manager", lambda: SimpleNamespace(health=lambda: payload))
    async with serve_http(runner.app) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == payload
