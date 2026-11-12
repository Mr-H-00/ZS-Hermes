from yuxi.modules.agents.presets import AgentPreset

PRESET = AgentPreset(
    slug="knowledge-base-qa",
    name="知识库问答",
    description="预加载知识库技能，基于可访问的知识库检索资料、核验原文并回答问题。",
    backend_id="ChatbotAgent",
    context={
        "skills": ["knowledge-base"],
        "preload_skills": ["knowledge-base"],
    },
)
