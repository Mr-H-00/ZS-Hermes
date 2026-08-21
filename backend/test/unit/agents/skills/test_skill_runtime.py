from types import SimpleNamespace

import pytest

import yuxi.agents.skills.runtime as skill_runtime
from yuxi.agents.skills.runtime import build_dependency_bundle, expand_skill_closure, resolve_runtime_skills_for_context


@pytest.mark.asyncio
async def test_resolve_runtime_skills_derives_authorized_scope(monkeypatch):
    """运行时 scope 只保留授权选择，并按依赖闭包区分共享与个人来源。"""

    async def fake_list_accessible_skills(db, user):
        assert db is not None
        assert user is not None
        return [
            SimpleNamespace(
                slug="alpha",
                name="Alpha",
                description="alpha desc",
                source_scope="shared",
                source_dir="/tmp/shared/alpha",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                source_scope="personal",
                source_dir="/tmp/personal/beta",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
            ),
        ]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)

    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha", "missing"]),
        db=object(),
        user=object(),
    )

    assert scope["context_skills"] == ["alpha"]
    assert scope["effective_skills"] == ["alpha", "beta"]
    assert set(scope["runtime_skills"]) == {"alpha", "beta"}
    assert scope["runtime_skills"]["alpha"]["path"] == "/home/gem/skills/alpha/SKILL.md"
    assert scope["runtime_skills"]["beta"]["path"] == "/home/gem/user-data/agents/skills/beta/SKILL.md"
    assert scope["runtime_skills"]["alpha"]["skills"] == ["beta"]


def test_expand_skill_closure_handles_cycles_missing_and_duplicates():
    """循环、缺失目标和重复依赖保持 fail-safe 且稳定去重。"""
    runtime_skills = {
        "alpha": {"tools": [], "mcps": [], "skills": ["beta", "missing", "beta"]},
        "beta": {"tools": [], "mcps": [], "skills": ["alpha"]},
    }

    assert expand_skill_closure(["alpha", "alpha"], runtime_skills) == ["alpha", "beta"]


def test_dependency_bundle_returns_only_consumed_dependencies():
    """依赖包只暴露 Middleware 消费的工具和 MCP 字段。"""
    runtime_skills = {
        "alpha": {"tools": ["tool-a", "tool-a"], "mcps": ["mcp-a"], "skills": ["beta"]},
        "beta": {"tools": ["tool-b"], "mcps": ["mcp-a", "mcp-b"], "skills": []},
    }

    bundle = build_dependency_bundle(["alpha", "beta"], runtime_skills)

    assert bundle == {"tools": ["tool-a", "tool-b"], "mcps": ["mcp-a", "mcp-b"]}
    assert "skills" not in bundle
