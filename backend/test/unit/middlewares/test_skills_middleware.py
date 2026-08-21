from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

import yuxi.agents.middlewares.skills as skills_middleware
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.skills.runtime import resolve_skill_gated_tools
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

_KB_TOOL_NAMES = {
    "list_kbs",
    "query_kb",
    "find_kb_document",
    "open_kb_document",
    "get_mindmap",
}


def _system_message_text(message: SystemMessage) -> str:
    return "\n".join(block.get("text", "") for block in message.content_blocks if isinstance(block, dict))


def _runtime_skill(
    slug: str,
    *,
    name: str | None = None,
    description: str = "",
    tools: list[str] | None = None,
) -> dict:
    return {
        "name": name or slug,
        "description": description,
        "path": f"/home/gem/skills/{slug}/SKILL.md",
        "tools": tools or [],
        "mcps": [],
        "skills": [],
    }


@pytest.mark.asyncio
async def test_skills_prompt_uses_effective_skills_at_request_level():
    context = SimpleNamespace(
        system_prompt="context base",
        skills=["configured-only"],
        _effective_skill_slugs=["alpha"],
        _runtime_skills={
            "alpha": _runtime_skill("alpha", name="Alpha", description="alpha desc"),
            "configured-only": _runtime_skill(
                "configured-only",
                name="Configured Only",
                description="should not appear",
            ),
        },
    )

    class FakeRequest:
        def __init__(self, *, system_message=None, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools or []
            self.system_message = system_message or SystemMessage(content="base")

        def override(self, **kwargs):
            return FakeRequest(
                system_message=kwargs.get("system_message", self.system_message),
                tools=kwargs.get("tools", self.tools),
            )

    captured = {}

    async def handler(request):
        captured["system_message"] = request.system_message
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(FakeRequest(), handler)
    prompt_text = _system_message_text(captured["system_message"])

    assert result == "ok"
    assert "base" in prompt_text
    assert "Alpha" in prompt_text
    assert "Configured Only" not in prompt_text
    assert context.system_prompt == "context base"
    assert not hasattr(context, "_skills_prompt_injected")
    assert not hasattr(context, "_visible_skills")


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_dependencies_only_for_readable_activated_skills(monkeypatch):
    monkeypatch.setattr(
        skills_middleware,
        "get_all_tool_instances",
        lambda: [SimpleNamespace(name="tool-a"), SimpleNamespace(name="tool-b")],
    )

    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _effective_skill_slugs=["alpha"],
                    _runtime_skills={
                        "alpha": _runtime_skill("alpha", tools=["tool-a"]),
                        "beta": _runtime_skill("beta", tools=["tool-b"]),
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": ["alpha", "beta"]}
            self.tools = tools or []

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = [tool.name for tool in request.tools]
        return "ok"

    result = await SkillsMiddleware(enable_skills_prompt=False).awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == ["tool-a"]


@pytest.mark.asyncio
async def test_awrap_model_call_mounts_knowledge_base_skill_tools():
    class FakeRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _effective_skill_slugs=["knowledge-base"],
                    _runtime_skills={
                        "knowledge-base": _runtime_skill(
                            "knowledge-base",
                            tools=[
                                "list_kbs",
                                "query_kb",
                                "find_kb_document",
                                "open_kb_document",
                                "get_mindmap",
                            ],
                        )
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": ["knowledge-base"]}
            self.tools = tools or []

        def override(self, *, tools):
            new_request = FakeRequest(tools=tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    captured = {}

    async def handler(request):
        captured["tools"] = {tool.name for tool in request.tools}
        return "ok"

    result = await SkillsMiddleware(enable_skills_prompt=False).awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert captured["tools"] == {
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
        "get_mindmap",
    }


@pytest.mark.asyncio
async def test_resolve_skill_gated_tools_registers_kb_tools():
    """门控工具必须能从可见 Skill 的依赖解析出真实工具实例，并随基础工具一起进入
    create_agent 工具列表（即注册进 ToolNode），否则激活后仍报 not a valid tool。"""
    context = SimpleNamespace(
        tools=None,
        mcps=None,
        _effective_skill_slugs=["knowledge-base"],
        _runtime_skills={"knowledge-base": _runtime_skill("knowledge-base", tools=sorted(_KB_TOOL_NAMES))},
    )

    gated_tools = resolve_skill_gated_tools(context)
    assert {tool.name for tool in gated_tools} == _KB_TOOL_NAMES

    runtime_tools = await resolve_configured_runtime_tools(context)
    assert _KB_TOOL_NAMES <= {tool.name for tool in runtime_tools}


def _make_gated_request(activated):
    base = SimpleNamespace(name="read_file")
    gated = [SimpleNamespace(name="list_kbs"), SimpleNamespace(name="query_kb")]

    class FakeRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    _effective_skill_slugs=["knowledge-base"],
                    _runtime_skills={
                        "knowledge-base": _runtime_skill("knowledge-base", tools=["list_kbs", "query_kb"])
                    },
                    mcps=[],
                )
            )
            self.state = {"activated_skills": activated}
            self.tools = tools

        def override(self, *, tools):
            new_request = FakeRequest(tools)
            new_request.runtime = self.runtime
            new_request.state = self.state
            return new_request

    # ToolNode 默认绑定 = 基础工具 + 门控工具
    return FakeRequest([base, *gated])


@pytest.mark.asyncio
async def test_awrap_model_call_hides_gated_tools_until_activated():
    """未激活 Skill 时门控工具对模型不可见（懒加载），激活后才放出。"""
    request = _make_gated_request(activated=[])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await SkillsMiddleware(enable_skills_prompt=False).awrap_model_call(request, handler)

    assert captured["tools"] == {"read_file"}


@pytest.mark.asyncio
async def test_awrap_model_call_keeps_gated_tools_when_activated():
    request = _make_gated_request(activated=["knowledge-base"])
    captured = {}

    async def handler(req):
        captured["tools"] = {tool.name for tool in req.tools}
        return "ok"

    await SkillsMiddleware(enable_skills_prompt=False).awrap_model_call(request, handler)

    assert captured["tools"] == {"read_file", "list_kbs", "query_kb"}


def test_read_file_activates_only_readable_skill() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(_effective_skill_slugs=["alpha"])),
        tool_call={"name": "read_file", "args": {"file_path": "/home/gem/skills/alpha/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert isinstance(updated, Command)
    assert updated.update["activated_skills"] == ["alpha"]


def test_personal_workspace_path_activates_skill() -> None:
    middleware = SkillsMiddleware()

    slug = middleware._extract_skill_slug_from_skill_md_path("/home/gem/user-data/agents/skills/alpha/SKILL.md")

    assert slug == "alpha"


def test_read_file_denies_skill_outside_readable_scope() -> None:
    middleware = SkillsMiddleware()
    result = ToolMessage(content="ok", tool_call_id="tool-1", name="read_file")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(_effective_skill_slugs=["alpha"])),
        tool_call={"name": "read_file", "args": {"file_path": "/home/gem/skills/beta/SKILL.md"}},
    )

    updated = middleware._process_tool_call_result(result, request)

    assert updated is result
