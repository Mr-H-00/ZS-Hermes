"""Skill 运行时解析。"""

from __future__ import annotations

from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.backends.paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from yuxi.agents.skills.service import list_accessible_skills, normalize_string_list
from yuxi.agents.toolkits import get_all_tool_instances
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


class RuntimeSkill(TypedDict):
    """单个 Skill 的运行时信息。"""

    name: str
    description: str
    path: str
    tools: list[str]
    mcps: list[str]
    skills: list[str]


def build_runtime_skills(skills: list) -> dict[str, RuntimeSkill]:
    """从已授权 Skill 构建运行时信息。"""
    result: dict[str, RuntimeSkill] = {}
    for item in skills:
        if not item.slug:
            continue
        root = (
            VIRTUAL_PERSONAL_SKILLS_PATH if getattr(item, "source_scope", None) == "personal" else VIRTUAL_SKILLS_PATH
        )
        result[item.slug] = {
            "name": item.name,
            "description": item.description,
            "path": f"{root}/{item.slug}/SKILL.md",
            "tools": normalize_string_list(item.tool_dependencies or []),
            "mcps": normalize_string_list(item.mcp_dependencies or []),
            "skills": normalize_string_list(item.skill_dependencies or []),
        }
    return result


def expand_skill_closure(
    slugs: list[str] | None,
    runtime_skills: dict[str, RuntimeSkill],
) -> list[str]:
    """展开 Skill 依赖闭包并保持根与依赖的声明顺序。"""
    ordered_roots = normalize_string_list(slugs)
    if not ordered_roots:
        return []

    result: list[str] = []
    seen: set[str] = set()

    def dfs(slug: str, stack: set[str]) -> None:
        if slug in stack:
            logger.warning(f"Cycle detected in skill dependencies, skip: {' -> '.join([*stack, slug])}")
            return
        if slug in seen:
            return

        node = runtime_skills.get(slug)
        if not node:
            logger.warning(f"Skill dependency target not found in DB, skip: {slug}")
            return

        seen.add(slug)
        result.append(slug)
        next_stack = set(stack)
        next_stack.add(slug)
        for dep in node.get("skills", []):
            dfs(dep, next_stack)

    for root in ordered_roots:
        dfs(root, set())
    return result


async def resolve_runtime_skills_for_context(
    context,
    *,
    db: AsyncSession,
    user: User,
) -> dict:
    """从已授权 Skill 派生当前 Agent Run 的运行时 scope。"""
    skill_items = await list_accessible_skills(db, user)
    runtime_skills = build_runtime_skills(skill_items)
    available = set(runtime_skills)
    selected = normalize_string_list(getattr(context, "skills", None))
    context_skills = [slug for slug in selected if slug in available]
    effective_skills = expand_skill_closure(context_skills, runtime_skills)
    return {
        "context_skills": context_skills,
        "effective_skills": effective_skills,
        "runtime_skills": runtime_skills,
    }


def resolve_skill_gated_tools(context) -> list:
    """解析所有可见 Skill 依赖且需注册到 ToolNode 的本地工具。"""
    runtime_skills = getattr(context, "_runtime_skills", {}) or {}
    effective_skills = getattr(context, "_effective_skill_slugs", []) or []
    tool_names: set[str] = set()
    for slug in effective_skills:
        node = runtime_skills.get(slug) or {}
        tool_names.update(node.get("tools", []))
    if not tool_names:
        return []
    return [tool for tool in get_all_tool_instances() if tool.name in tool_names]


def build_dependency_bundle(
    activated_skills: list[str],
    runtime_skills: dict[str, RuntimeSkill],
) -> dict[str, list[str]]:
    """汇总直接激活 Skill 的本地工具和 MCP 依赖。"""
    tools: list[str] = []
    mcps: list[str] = []
    seen_tools: set[str] = set()
    seen_mcps: set[str] = set()

    for slug in activated_skills:
        dependency = runtime_skills.get(slug, {})
        for tool_name in dependency.get("tools", []):
            if tool_name in seen_tools:
                continue
            seen_tools.add(tool_name)
            tools.append(tool_name)
        for mcp_name in dependency.get("mcps", []):
            if mcp_name in seen_mcps:
                continue
            seen_mcps.add(mcp_name)
            mcps.append(mcp_name)

    return {"tools": tools, "mcps": mcps}
