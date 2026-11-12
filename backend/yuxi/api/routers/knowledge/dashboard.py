"""知识域 Dashboard HTTP 路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from yuxi.api.dependencies.auth import get_superadmin_user
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.services.dashboard import get_knowledge_stats

knowledge_dashboard = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class KnowledgeStats(BaseModel):
    """知识库统计。"""

    total_knowledge_bases: int
    total_files: int
    total_nodes: int
    total_storage_size: int
    knowledge_bases_by_type: dict
    file_type_distribution: dict


@knowledge_dashboard.get("/stats/knowledge", response_model=KnowledgeStats)
async def read_knowledge_stats(
    current_user: User = Depends(get_superadmin_user),
):
    """获取知识库统计（超级管理员权限）。"""

    try:
        return KnowledgeStats(**await get_knowledge_stats())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error getting knowledge stats")
        raise HTTPException(status_code=500, detail=f"Failed to get knowledge stats: {str(exc)}") from exc
