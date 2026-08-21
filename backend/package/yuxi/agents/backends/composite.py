from __future__ import annotations

from dataclasses import dataclass

from deepagents.backends.composite import (
    CompositeBackend,
    _remap_file_info_path,
    _route_for_path,
    _strip_route_from_pattern,
)
from deepagents.backends.protocol import FileInfo, GlobResult
from deepagents.middleware.filesystem import FilesystemMiddleware

from yuxi.agents.backends.paths import runtime_workdir_path, workdir_runtime_paths
from yuxi.agents.skills.service import refresh_user_skill_projection_async

from .sandbox import ProvisionerSandboxBackend

_TOOL_RESULT_EVICTION_EXEMPT_TOOLS = frozenset({"read_file", "open_kb_document"})


def _coerce_glob_result(result) -> GlobResult:
    if isinstance(result, GlobResult):
        return result
    return GlobResult(matches=result or [])


class CustomCompositeBackend(CompositeBackend):
    """修复 glob 路由逻辑的 CompositeBackend。"""

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(backend.glob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(self.default.glob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(backend.glob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(self.default.glob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        backend, backend_path, route_prefix = _route_for_path(
            default=self.default,
            sorted_routes=self.sorted_routes,
            path=path,
        )
        if route_prefix is not None:
            result = _coerce_glob_result(await backend.aglob(pattern, backend_path))
            if result.error:
                return result
            return GlobResult(matches=[_remap_file_info_path(fi, route_prefix) for fi in (result.matches or [])])

        if path is None or path == "/":
            results: list[FileInfo] = []
            default_result = _coerce_glob_result(await self.default.aglob(pattern, path))
            if default_result.error:
                return default_result
            results.extend(default_result.matches or [])
            for route_prefix, backend in self.routes.items():
                route_pattern = _strip_route_from_pattern(pattern, route_prefix)
                result = _coerce_glob_result(await backend.aglob(route_pattern, "/"))
                if result.error:
                    return result
                results.extend(_remap_file_info_path(fi, route_prefix) for fi in (result.matches or []))
            results.sort(key=lambda x: x.get("path", ""))
            return GlobResult(matches=results)

        return _coerce_glob_result(await self.default.aglob(pattern, path))


class YuxiFilesystemMiddleware(FilesystemMiddleware):
    """Filesystem middleware that budgets large tool outputs before they hit model context."""

    def wrap_tool_call(self, request, handler):
        tool_result = handler(request)

        if self._tool_token_limit_before_evict is None:
            return tool_result
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result

        return self._intercept_large_tool_result(tool_result, request.runtime)

    async def awrap_tool_call(self, request, handler):
        tool_result = await handler(request)

        if self._tool_token_limit_before_evict is None:
            return tool_result
        if request.tool_call["name"] in _TOOL_RESULT_EVICTION_EXEMPT_TOOLS:
            return tool_result

        return await self._aintercept_large_tool_result(tool_result, request.runtime)


@dataclass(frozen=True)
class _BackendScope:
    runtime_scope_id: str
    workdir_relative_path: str
    uid: str

    @property
    def workdir_path(self) -> str:
        return runtime_workdir_path(self.workdir_relative_path)

    @classmethod
    def from_runtime(cls, runtime) -> _BackendScope:
        config = getattr(runtime, "config", None)
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        context = getattr(runtime, "context", None)
        state = getattr(runtime, "state", None)
        return cls.from_sources(
            configurable if isinstance(configurable, dict) else {},
            context,
            state if isinstance(state, dict) else {},
            error_context="runtime configurable context",
        )

    @classmethod
    def from_sources(cls, *sources, error_context: str) -> _BackendScope:
        def string_value(key: str) -> str | None:
            for source in sources:
                value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        thread_id = string_value("thread_id")
        if not thread_id:
            raise ValueError(f"thread_id is required in {error_context}")

        uid = string_value("uid")
        if not uid:
            raise ValueError(f"uid is required in {error_context}")

        runtime_scope_id = string_value("runtime_scope_id") or thread_id
        relative_path = string_value("workdir_relative_path") or ""
        return cls(
            runtime_scope_id=runtime_scope_id,
            workdir_relative_path=relative_path,
            uid=uid,
        )

    def create_backend(self) -> CompositeBackend:
        if not self.workdir_relative_path:
            raise ValueError("workdir path is required in runtime context")
        return CustomCompositeBackend(
            default=ProvisionerSandboxBackend(
                thread_id=self.runtime_scope_id,
                uid=self.uid,
                workdir_path=self.workdir_relative_path,
                create_if_missing=False,
            ),
            routes={},
            artifacts_root=self.workdir_path,
        )


async def sync_agent_context_skills(context) -> None:
    """在 Agent Run 初始化时同步当前用户获授权的共享 Skill 投影。"""
    scope = _BackendScope.from_sources(context, error_context="runtime context")
    await refresh_user_skill_projection_async(scope.uid)


def create_agent_composite_backend(runtime) -> CompositeBackend:
    return _BackendScope.from_runtime(runtime).create_backend()


def create_agent_filesystem_middleware(
    tool_token_limit_before_evict: int | None = None,
    *,
    context,
) -> FilesystemMiddleware:
    scope = _BackendScope.from_sources(
        context,
        error_context="runtime context",
    )

    def build_context_backend(_runtime):
        """按可变运行上下文重建文件作用域，读取已同步的共享 Skill 投影。"""
        return scope.create_backend()

    middleware = YuxiFilesystemMiddleware(
        backend=build_context_backend,
        tool_token_limit_before_evict=tool_token_limit_before_evict,
    )
    large_results, history = workdir_runtime_paths(scope.workdir_path)
    middleware._large_tool_results_prefix = large_results
    middleware._conversation_history_prefix = history
    return middleware
