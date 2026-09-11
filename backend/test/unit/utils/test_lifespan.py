"""API startup/readiness 组件与补偿清理测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from server.utils import lifespan as lifespan_module

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_optional_startup_component_failure_is_structured_without_raw_message() -> None:
    app = FastAPI()
    app.state.startup_components = {}

    async def fail() -> None:
        raise RuntimeError("database-password-must-not-leak")

    await lifespan_module._initialize_startup_component(
        app,
        name="builtin_mcp_servers",
        required=False,
        operation=fail,
    )

    assert app.state.startup_components == {
        "builtin_mcp_servers": {"status": "error", "required": False, "code": "RuntimeError"}
    }
    assert "password" not in str(app.state.startup_components)


async def test_invalid_security_secrets_fail_before_database_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_KEY_DERIVATION_SECRET", raising=False)

    def forbidden_initialize() -> None:
        raise AssertionError("database startup must not begin")

    monkeypatch.setattr(lifespan_module.pg_manager, "initialize", forbidden_initialize)
    app = FastAPI()

    with pytest.raises(
        lifespan_module.RequiredStartupComponentError,
        match="component=security_secrets, type=ValueError",
    ):
        await lifespan_module._startup(app)

    assert app.state.startup_components == {
        "security_secrets": {"status": "error", "required": True, "code": "ValueError"}
    }


async def test_api_startup_validates_full_schema_without_running_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.delenv("LITE_MODE", raising=False)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "schema-test-api-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("JWT_SECRET_KEY", "schema-test-jwt-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "schema-test-sandbox-token-at-least-thirty-two-characters")
    monkeypatch.setattr(lifespan_module.pg_manager, "initialize", lambda: calls.append("initialize"))

    async def require_current_schema(*, include_knowledge: bool) -> None:
        calls.append(("require_current_schema", include_knowledge))
        raise RuntimeError("stop after schema assertion")

    monkeypatch.setattr(lifespan_module.pg_manager, "require_current_schema", require_current_schema)

    with pytest.raises(RuntimeError, match="stop after schema assertion"):
        await lifespan_module._startup(FastAPI())

    assert calls == ["initialize", ("require_current_schema", True)]


async def test_lite_api_startup_only_requires_business_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LITE API 启动不得要求 knowledge schema。"""

    calls: list[object] = []
    monkeypatch.setenv("LITE_MODE", "true")
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "schema-test-api-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("JWT_SECRET_KEY", "schema-test-jwt-secret-at-least-thirty-two-characters")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "schema-test-sandbox-token-at-least-thirty-two-characters")
    monkeypatch.setattr(lifespan_module.pg_manager, "initialize", lambda: calls.append("initialize"))

    async def require_current_schema(*, include_knowledge: bool) -> None:
        """记录 API 请求的 schema 范围并终止后续启动。"""

        calls.append(("require_current_schema", include_knowledge))
        raise RuntimeError("stop after schema assertion")

    monkeypatch.setattr(lifespan_module.pg_manager, "require_current_schema", require_current_schema)

    with pytest.raises(RuntimeError, match="stop after schema assertion"):
        await lifespan_module._startup(FastAPI())

    assert calls == ["initialize", ("require_current_schema", False)]


async def test_required_startup_component_failure_still_releases_every_runtime_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[str] = []
    monkeypatch.delenv("LITE_MODE", raising=False)

    async def fail_startup(app: FastAPI) -> None:
        async def fail() -> None:
            raise RuntimeError("database-password-must-not-leak")

        await lifespan_module._initialize_startup_component(
            app,
            name="default_agents",
            required=True,
            operation=fail,
        )

    async def record_shutdown(name: str, operation) -> None:
        del operation
        released.append(name)

    monkeypatch.setattr(lifespan_module, "_startup", fail_startup)
    monkeypatch.setattr(lifespan_module, "_shutdown_component", record_shutdown)

    app = FastAPI()
    with pytest.raises(
        lifespan_module.RequiredStartupComponentError,
        match="component=default_agents, type=RuntimeError",
    ) as exc_info:
        async with lifespan_module.lifespan(app):
            raise AssertionError("startup failure must prevent yield")

    assert app.state.startup_components == {
        "default_agents": {"status": "error", "required": True, "code": "RuntimeError"}
    }
    assert "password" not in str(exc_info.value)
    assert released == ["sandbox_provider", "queue_clients", "neo4j", "postgres"]


async def test_lite_shutdown_never_loads_neo4j_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """LITE 补偿清理不得通过 Neo4j shutdown 反向加载图谱模块。"""

    released: list[str] = []
    monkeypatch.setenv("LITE_MODE", "true")

    async def fail_startup(app: FastAPI) -> None:
        """在取得业务资源前模拟启动失败。"""

        del app
        raise RuntimeError("startup failed")

    async def record_shutdown(name: str, operation) -> None:
        """记录补偿清理组件而不执行真实关闭。"""

        del operation
        released.append(name)

    monkeypatch.setattr(lifespan_module, "_startup", fail_startup)
    monkeypatch.setattr(lifespan_module, "_shutdown_component", record_shutdown)

    with pytest.raises(RuntimeError, match="startup failed"):
        async with lifespan_module.lifespan(FastAPI()):
            raise AssertionError("startup failure must prevent yield")

    assert released == ["sandbox_provider", "queue_clients", "postgres"]
