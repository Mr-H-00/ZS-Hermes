"""显式注册业务和知识 ORM 映射，供 Schema 初始化与测试独立装配。"""

from importlib import import_module

from zs.infrastructure.postgres.base import BusinessBase, KnowledgeBase

MODEL_MODULES = (
    "zs.modules.agents.models.definitions",
    "zs.modules.agents.models.sessions",
    "zs.modules.agents.models.cooperation",
    "zs.modules.agents.models.inputs",
    "zs.modules.agents.models.attachments",
    "zs.modules.agents.models.turns",
    "zs.modules.agents.models.runs",
    "zs.modules.agents.models.sandbox",
    "zs.modules.agents.models.messages",
    "zs.modules.agents.models.feedback",
    "zs.modules.workspace.models",
    "zs.modules.identity.models",
    "zs.modules.extensions.skills.models",
    "zs.modules.extensions.mcp.models",
    "zs.modules.models.tables",
    "zs.modules.system.models",
    "zs.modules.background_jobs.models",
    "zs.modules.schedules.models",
    "zs.modules.knowledge.models",
)


def load_models() -> tuple:
    """加载全部映射并返回两个独立 metadata。"""
    for module in MODEL_MODULES:
        import_module(module)
    return BusinessBase.metadata, KnowledgeBase.metadata
