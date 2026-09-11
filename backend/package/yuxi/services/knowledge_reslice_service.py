"""知识文档显式重切用例。"""

from __future__ import annotations

import hashlib
import json

from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import Task, Tasker, tasker
from yuxi.storage.postgres.manager import pg_manager


class ResliceTargetError(ValueError):
    """重切目标不存在、不可见或不是文档时抛出的错误。"""


class ResliceConflictError(RuntimeError):
    """目标文件已存在活动重切任务时抛出的错误。"""


class KnowledgeResliceService:
    """编排显式重切任务、文件归属校验和持久任务冲突判断。"""

    def __init__(
        self,
        *,
        repository: KnowledgeFileRepository | None = None,
        task_repository: TaskRepository | None = None,
        tasker_instance: Tasker | None = None,
    ) -> None:
        """注入文件仓储、任务仓储与持久 Task 门面。"""
        self.repository = repository or KnowledgeFileRepository()
        self.task_repository = task_repository or TaskRepository()
        self.tasker = tasker_instance or tasker

    @staticmethod
    def fingerprint(kb_id: str, file_ids: list[str], params: dict) -> str:
        """为重切请求生成稳定指纹，供 Tasker 并发去重。"""
        payload = json.dumps(
            {"kb_id": kb_id, "file_ids": sorted(set(file_ids)), "params": params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    async def validate_files(self, kb_id: str, file_ids: list[str]) -> None:
        """确认重切目标属于当前知识库且不是文件夹。"""
        records = await self.repository.list_by_file_ids(file_ids)
        records_by_id = {record.file_id: record for record in records}
        invalid = [
            file_id
            for file_id in file_ids
            if (record := records_by_id.get(file_id)) is None or record.kb_id != kb_id or bool(record.is_folder)
        ]
        if invalid:
            raise ResliceTargetError(f"重切目标文件不可见或不存在: {invalid}")

    async def enqueue(
        self,
        *,
        kb_id: str,
        file_ids: list[str],
        params: dict,
        operator_id: str,
        database_name: str,
    ) -> dict:
        """在 PostgreSQL 原子边界内提交重切任务并拒绝文件重叠。"""
        normalized_file_ids = list(dict.fromkeys(file_ids))
        fingerprint = self.fingerprint(kb_id, normalized_file_ids, params)
        task_payload = {
            "kb_id": kb_id,
            "file_ids": normalized_file_ids,
            "params": params,
            "operator_id": operator_id,
            "fingerprint": fingerprint,
        }

        async with pg_manager.get_async_session_context() as session:
            await self.task_repository.lock_submission_scope_in_session(
                session,
                f"knowledge-reslice:{kb_id}",
            )
            active_tasks = await self.task_repository.list_active_by_payload_in_session(
                session,
                task_type="knowledge_reslice",
                payload_match={"kb_id": kb_id},
            )

            equivalent = next(
                (record for record in active_tasks if (record.payload or {}).get("fingerprint") == fingerprint),
                None,
            )
            if equivalent is not None:
                task = Task.from_dict(equivalent.to_dict())
                created = False
            else:
                requested_file_ids = set(normalized_file_ids)
                conflicting_file_ids = sorted(
                    requested_file_ids.intersection(
                        file_id for record in active_tasks for file_id in ((record.payload or {}).get("file_ids") or [])
                    )
                )
                if conflicting_file_ids:
                    raise ResliceConflictError(f"目标文件已有正在执行的重切任务: {conflicting_file_ids}")

                task, created = await self.tasker.create_unique_in_session(
                    session,
                    name=f"文档重切 ({database_name})",
                    task_type="knowledge_reslice",
                    payload=task_payload,
                    payload_match={"kb_id": kb_id, "fingerprint": fingerprint},
                )

        if created:
            await self.tasker.publish(task)
        return {
            "message": "重切任务已提交" if created else "相同重切任务正在执行",
            "status": "queued",
            "task_id": task.id,
            "fingerprint": fingerprint,
        }


knowledge_reslice_service = KnowledgeResliceService()

__all__ = [
    "KnowledgeResliceService",
    "ResliceConflictError",
    "ResliceTargetError",
    "knowledge_reslice_service",
]
