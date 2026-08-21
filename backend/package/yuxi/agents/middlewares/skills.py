"""Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Annotated, Any, NotRequired

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from yuxi.agents.backends.paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from yuxi.agents.mcp.service import get_enabled_mcp_tools
from yuxi.agents.skills.runtime import RuntimeSkill, build_dependency_bundle
from yuxi.agents.skills.service import is_valid_skill_slug, normalize_string_list
from yuxi.agents.toolkits import get_all_tool_instances
from yuxi.utils.logging_config import logger


def _activated_skills_reducer(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并 activated_skills 列表"""
    return normalize_string_list([*(left or []), *(right or [])])


class SkillsState(AgentState):
    """Skills 状态定义"""

    activated_skills: NotRequired[Annotated[list[str], _activated_skills_reducer]]


class SkillsMiddleware(AgentMiddleware):
    """Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活

    职责：
    - Skills 提示词注入（直接从数据库加载）
    - 依赖展开（用户配置 + 动态激活）
    - 工具/MCP 动态加载
    """

    state_schema = SkillsState

    def __init__(
        self,
        *,
        enable_skills_prompt: bool = True,
        skills_sources_for_prompt: list[str] | None = None,
    ):
        """初始化中间件

        Args:
            enable_skills_prompt: 是否启用 skills 提示段注入（默认 True）
            skills_sources_for_prompt: skills 来源路径（默认展示共享投影与个人 Workspace）
        """
        super().__init__()
        self.enable_skills_prompt = enable_skills_prompt
        self.skills_sources_for_prompt = skills_sources_for_prompt or [
            f"{VIRTUAL_SKILLS_PATH}/",
            f"{VIRTUAL_PERSONAL_SKILLS_PATH}/",
        ]

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """包装模型调用，处理 skills 提示词注入、动态激活和依赖展开"""
        runtime_context = request.runtime.context

        if self.enable_skills_prompt:
            effective_skills = getattr(runtime_context, "_effective_skill_slugs", None)
            if isinstance(effective_skills, list):
                effective_skills = normalize_string_list(effective_skills)
                if effective_skills:
                    skills_meta = self._collect_prompt_metadata(effective_skills, runtime_context)
                    if skills_meta:
                        skills_section = self._build_skills_section(skills_meta)
                        system_message = append_to_system_message(
                            getattr(request, "system_message", None), skills_section
                        )
                        request = request.override(system_message=system_message)

        state = request.state if isinstance(request.state, dict) else {}
        activated = state.get("activated_skills", []) or []
        if not isinstance(activated, list):
            activated = []

        effective_skills = self._get_effective_skills(runtime_context)
        activated = [slug for slug in normalize_string_list(activated) if slug in effective_skills]

        deps_bundle = build_dependency_bundle(activated, self._get_runtime_skills(runtime_context))
        activated_tool_names = set(deps_bundle["tools"])

        # 门控：未激活 Skill 的依赖工具对模型不可见（保持按需加载）。
        # 这些工具已在构建期由 resolve_configured_runtime_tools 注册进 ToolNode，剔除只影响模型可见性、不影响可执行性。
        # 排除基础工具集中的工具（如 present_artifacts），它们始终可见、不受 Skill 激活影响。
        gated_tool_names = self._resolve_gated_tool_names(runtime_context) - activated_tool_names
        model_tools = list(request.tools or [])
        if gated_tool_names:
            model_tools = [t for t in model_tools if t.name not in gated_tool_names]

        # 追加已激活 Skill 的依赖工具：本地工具确保绑定给模型，MCP 工具按需加载
        enabled_tools = []
        if activated_tool_names:
            enabled_tools = [t for t in get_all_tool_instances() if t.name in activated_tool_names]
        if deps_bundle["mcps"]:
            enabled_tools.extend(
                await self._get_mcp_tools_from_context(runtime_context, extra_mcps=deps_bundle["mcps"])
            )

        existing_tool_names = {t.name for t in model_tools}
        for t in enabled_tools:
            if t.name not in existing_tool_names:
                model_tools.append(t)
                existing_tool_names.add(t.name)

        if gated_tool_names or enabled_tools:
            request = request.override(tools=model_tools)

        return await handler(request)

    def _resolve_gated_tool_names(self, runtime_context) -> set[str]:
        """所有可见 Skill 依赖、且不属于基础工具集的工具名集合（即「仅经 Skill 激活才放出」的工具）。"""
        runtime_skills = self._get_runtime_skills(runtime_context)
        effective_skills = self._get_effective_skills(runtime_context)
        base_tool_names = set(normalize_string_list(getattr(runtime_context, "tools", None)))
        gated: set[str] = set()
        for slug in effective_skills:
            gated.update(runtime_skills.get(slug, {}).get("tools", []))
        return gated - base_tool_names

    def _collect_prompt_metadata(self, slugs: list[str], runtime_context) -> list[RuntimeSkill]:
        """收集指定 slugs 的提示词元数据"""
        runtime_skills = self._get_runtime_skills(runtime_context)
        result: list[RuntimeSkill] = []
        for slug in slugs:
            item = runtime_skills.get(slug)
            if not item:
                logger.debug(f"Skill slug not found in prompt metadata, skip: {slug}")
                continue
            result.append(dict(item))
        return result

    async def _get_mcp_tools_from_context(
        self,
        context,
        *,
        extra_mcps: list[str] | None = None,
    ) -> list:
        """从上下文配置中获取 MCP 工具列表"""
        import asyncio

        # MCP 工具（并行加载）
        mcps = getattr(context, "mcps", None) or []
        all_mcp_names: list[str] = []
        for server_name in mcps:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)
        for server_name in extra_mcps or []:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)

        # 去重
        unique_mcp_names = list(dict.fromkeys(all_mcp_names))

        async def load_mcp_tools(server_name: str) -> list:
            """加载单个 MCP 服务器的工具"""
            try:
                mcp_tools = await get_enabled_mcp_tools(server_name)
                if not mcp_tools:
                    logger.warning(f"SkillsMiddleware: mcp dependency unavailable, skip: {server_name}")
                return mcp_tools
            except Exception as e:
                logger.warning(f"SkillsMiddleware: failed to load mcp dependency '{server_name}': {e}")
                return []

        # 并行加载所有 MCP 工具
        results = await asyncio.gather(*[load_mcp_tools(name) for name in unique_mcp_names])
        selected_tools = []
        for tools in results:
            selected_tools.extend(tools)

        return selected_tools

    def _process_tool_call_result(self, result: Any, request: ToolCallRequest) -> Any:
        """处理工具调用结果，检查并处理 skill 动态激活"""
        if request.tool_call.get("name") != "read_file":
            return result

        args = request.tool_call.get("args") or {}
        file_path = args.get("file_path") if isinstance(args, dict) else None
        slug = self._extract_skill_slug_from_skill_md_path(file_path)

        if not slug:
            return result

        if slug not in self._get_effective_skills(request.runtime.context):
            logger.warning(f"SkillsMiddleware: deny skill activation for invisible slug: {slug}")
            return result

        logger.debug(f"SkillsMiddleware: activated skill by read_file: {slug}")
        return self._merge_activated_skill_update(result, slug)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """包装工具调用，处理 skill 动态激活"""
        result = await handler(request)
        return self._process_tool_call_result(result, request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """同步版本的工具调用包装"""
        result = handler(request)
        return self._process_tool_call_result(result, request)

    def _extract_skill_slug_from_skill_md_path(self, file_path: Any) -> str | None:
        """从共享投影或个人 UserWorkspace 的 SKILL.md 路径中提取 slug。"""
        if not isinstance(file_path, str):
            return None
        raw = file_path.strip()
        if not raw:
            return None
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        for root in (VIRTUAL_SKILLS_PATH, VIRTUAL_PERSONAL_SKILLS_PATH):
            try:
                relative = pure.relative_to(PurePosixPath(root))
            except ValueError:
                continue
            if len(relative.parts) == 2 and relative.name == "SKILL.md" and is_valid_skill_slug(relative.parts[0]):
                return relative.parts[0]
        return None

    def _get_effective_skills(self, runtime_context) -> set[str]:
        selected = getattr(runtime_context, "_effective_skill_slugs", [])
        return set(normalize_string_list(selected if isinstance(selected, list) else []))

    def _get_runtime_skills(self, runtime_context) -> dict[str, RuntimeSkill]:
        runtime_skills = getattr(runtime_context, "_runtime_skills", {})
        return runtime_skills if isinstance(runtime_skills, dict) else {}

    def _merge_activated_skill_update(self, result: Any, slug: str):
        """合并动态激活的 skill 更新"""
        from langchain_core.messages import ToolMessage

        if isinstance(result, Command):
            update = dict(result.update or {})
            current = update.get("activated_skills") or []
            update["activated_skills"] = _activated_skills_reducer(current, [slug])
            return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], "activated_skills": [slug]})

        return result

    def _format_skills_locations(self, sources: list[str]) -> str:
        """格式化 skills 位置信息"""
        locations = []
        for i, source_path in enumerate(sources):
            name = PurePosixPath(source_path.rstrip("/")).name.capitalize()
            suffix = " (higher priority)" if i == len(sources) - 1 else ""
            locations.append(f"**{name} Skills**: `{source_path}`{suffix}")
        return "\n".join(locations)

    def _format_skills_list(self, skills_meta: list[dict[str, str]]) -> str:
        """格式化 skills 列表"""
        if not skills_meta:
            return f"(No skills available yet. You can create skills in {' or '.join(self.skills_sources_for_prompt)})"

        lines = []
        for skill in skills_meta:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> Read `{skill['path']}` for full instructions")
        return "\n".join(lines)

    def _build_skills_section(self, skills_meta: list[dict[str, str]]) -> str:
        """构建 skills 提示段"""
        skills_locations = self._format_skills_locations(self.skills_sources_for_prompt)
        skills_list = self._format_skills_list(skills_meta)
        return SKILLS_SYSTEM_PROMPT.format(
            skills_locations=skills_locations,
            skills_load_warnings="",
            skills_list=skills_list,
        )
