"""版本化 Knowledge Public API 路由注册。"""

from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from yuxi.api.dependencies.auth import get_required_user
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.base import KBNotFoundError
from yuxi.modules.knowledge.schemas import FindInputSchema, OpenInputSchema, SearchFileInput, SearchInputSchema
from yuxi.modules.knowledge.services import tools as knowledge_tools
from yuxi.modules.knowledge.services.access import visible_knowledge_bases

public_knowledge_router = APIRouter(prefix="/v1")

tool_router = APIRouter(prefix="/knowledge/tools", tags=["knowledge"])


async def _result(operation: Awaitable[Any]) -> Any:
    """将服务层输入与权限错误转换为 HTTP 结果。"""
    try:
        return await operation
    except knowledge_tools.KnowledgeToolError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 400, detail=str(exc)) from exc
    except KBNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@tool_router.get("/list_kbs")
async def list_kbs(current_user: User = Depends(get_required_user)):
    """列出当前用户可见的知识库。"""
    return knowledge_tools.list_kbs(await visible_knowledge_bases(current_user.uid))


@tool_router.post("/query_kb")
async def query_kb(payload: SearchInputSchema, current_user: User = Depends(get_required_user)):
    """在可见知识库中检索。"""
    return await _result(
        knowledge_tools.query_kb(
            payload.kb_id,
            payload.query_text,
            await visible_knowledge_bases(current_user.uid),
            file_name=payload.file_name,
        )
    )


@tool_router.post("/open_kb_document")
async def open_kb_document(payload: OpenInputSchema, current_user: User = Depends(get_required_user)):
    """按行打开可见知识库文档。"""
    return await _result(
        knowledge_tools.open_kb_document(
            payload.kb_id,
            payload.file_id,
            await visible_knowledge_bases(current_user.uid),
            line=payload.line,
            offset=payload.offset,
            window_size=payload.window_size,
        )
    )


@tool_router.post("/find_kb_document")
async def find_kb_document(payload: FindInputSchema, current_user: User = Depends(get_required_user)):
    """定位可见知识库文档中的内容。"""
    return await _result(
        knowledge_tools.find_kb_document(
            payload.kb_id,
            payload.file_id,
            payload.patterns,
            await visible_knowledge_bases(current_user.uid),
            use_regex=payload.use_regex,
            case_sensitive=payload.case_sensitive,
            max_windows=payload.max_windows,
            window_size=payload.window_size,
        )
    )


@tool_router.post("/search_file")
async def search_file(payload: SearchFileInput, current_user: User = Depends(get_required_user)):
    """按名称搜索当前用户可见的知识库文件。"""
    return await _result(
        knowledge_tools.search_file(
            await visible_knowledge_bases(current_user.uid),
            kb_id=payload.kb_id,
            kb_name=payload.kb_name,
            query=payload.query,
            offset=payload.offset,
            limit=payload.limit,
        )
    )


public_knowledge_router.include_router(tool_router)
