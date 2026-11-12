"""在知识库准入事务内登记显式重切，复用当前代次化索引执行器。"""

import hashlib
import json

from sqlalchemy import select

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.modules.background_jobs.repository import BackgroundJobRepository
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError


async def submit_reslice(kb_id: str, file_ids: list[str], params: dict, operator_id: str) -> dict:
    """校验目标、拒绝重叠并去重相同请求，提交后派发返回持久作业身份。"""
    file_ids = list(dict.fromkeys(file_ids))
    fingerprint = hashlib.sha256(
        json.dumps({"file_ids": sorted(file_ids), "params": params}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    async with pg_manager.get_async_session_context() as session:
        kb = await DirectoryRepository().lock(session, kb_id)
        if kb.kb_type.lower() != "milvus":
            raise InvalidOperationError("仅 Milvus 知识库支持重切片")
        await DocumentResetRepository.require_processing(session, kb_id, file_ids=file_ids)
        files = list(
            (
                await session.execute(
                    select(KnowledgeFile).where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id.in_(file_ids),
                        KnowledgeFile.deleted_at.is_(None),
                        KnowledgeFile.is_folder.is_(False),
                    )
                )
            ).scalars()
        )
        if {file.file_id for file in files} != set(file_ids):
            raise ResourceNotFoundError("重切目标不存在或不属于该知识库")
        if any(not file.markdown_file for file in files):
            raise InvalidOperationError("请先解析所选文档，再执行重切片")
        active = await BackgroundJobRepository.list_active_by_payload_in_session(
            session, job_type="knowledge_reslice", payload_match={"kb_id": kb_id}
        )
        equivalent = next((job for job in active if (job.payload or {}).get("fingerprint") == fingerprint), None)
        created = False
        if equivalent is not None:
            job = equivalent
        else:
            requested = set(file_ids)
            if any(requested.intersection((job.payload or {}).get("file_ids", [])) for job in active):
                raise StateConflictError("目标文件已有正在执行的重切任务")
            job, created = await register_job_in_session(
                session,
                name=f"文档重切 ({kb.name})",
                job_type="knowledge_reslice",
                payload={
                    "kb_id": kb_id,
                    "file_ids": file_ids,
                    "params": params,
                    "operator_id": operator_id,
                    "fingerprint": fingerprint,
                },
                payload_match={"kb_id": kb_id, "fingerprint": fingerprint},
            )
    if created:
        await dispatch_job(job.id)
    return {
        "message": "重切任务已提交" if created else "相同重切任务正在执行",
        "status": "queued",
        "job_id": job.id,
        "fingerprint": fingerprint,
    }
