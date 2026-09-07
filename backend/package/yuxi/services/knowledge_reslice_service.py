"""知识文档显式重切用例。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext, Tasker, TaskPayloadConflictError, tasker


class ResliceTargetError(ValueError):
    """重切目标不存在、不可见或不是文档时抛出的错误。"""


class ResliceConflictError(RuntimeError):
    """目标文件已存在活动重切任务时抛出的错误。"""


class KnowledgeResliceService:
    """编排显式重切任务、文件归属校验和 Tasker 去重。"""

    def __init__(
        self,
        *,
        manager: Any | None = None,
        repository: KnowledgeFileRepository | None = None,
        tasker_instance: Tasker | None = None,
    ) -> None:
        self.manager = manager or knowledge_base
        self.repository = repository or KnowledgeFileRepository()
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

    @staticmethod
    def _payloads_overlap(existing_payload: dict[str, Any], requested_payload: dict[str, Any]) -> bool:
        """判断两个重切 payload 是否属于同一知识库且包含相同文件。"""
        if existing_payload.get("kb_id") != requested_payload.get("kb_id"):
            return False
        existing_file_ids = set(existing_payload.get("file_ids") or [])
        requested_file_ids = set(requested_payload.get("file_ids") or [])
        return bool(existing_file_ids & requested_file_ids)

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

    async def run(
        self,
        *,
        context: TaskContext,
        kb_id: str,
        file_ids: list[str],
        operator_id: str,
        params: dict,
    ) -> dict:
        """执行显式重切任务，逐文件返回结果并保留失败项。"""
        await context.set_message("准备重切文档")
        await context.set_progress(5.0, "准备重切文档")
        processed_items: list[dict] = []
        total = len(file_ids)
        for index, file_id in enumerate(file_ids, start=1):
            await context.raise_if_cancelled()
            await context.set_progress(5.0 + index / total * 90.0, f"正在重切第 {index}/{total} 个文档")
            try:
                processed_items.append(
                    await self.manager.reslice_file(
                        kb_id,
                        file_id,
                        operator_id=operator_id,
                        params=params,
                    )
                )
            except Exception as error:  # noqa: BLE001
                processed_items.append({"file_id": file_id, "status": "failed", "error": str(error)})

        failed_count = sum(1 for item in processed_items if item.get("status") == "failed" or item.get("error"))
        result = {"items": processed_items, "processed": len(processed_items), "failed": failed_count}
        await context.set_result(result)
        await context.set_progress(100.0, f"重切完成，失败 {failed_count} 个" if failed_count else "重切完成")
        return result

    async def enqueue(
        self,
        *,
        kb_id: str,
        file_ids: list[str],
        params: dict,
        operator_id: str,
        database_name: str,
    ) -> dict:
        """提交显式重切任务并复用同 fingerprint 的活动任务。"""
        fingerprint = self.fingerprint(kb_id, file_ids, params)
        task_payload = {
            "kb_id": kb_id,
            "file_ids": file_ids,
            "params": params,
            "fingerprint": fingerprint,
        }

        async def run_reslice(context: TaskContext):
            """执行当前提交所绑定的显式重切任务。"""
            return await self.run(
                context=context,
                kb_id=kb_id,
                file_ids=file_ids,
                operator_id=operator_id,
                params=params,
            )

        try:
            task, created = await self.tasker.enqueue_unique_by_payload(
                name=f"文档重切 ({database_name})",
                task_type="knowledge_reslice",
                payload=task_payload,
                payload_match={"kb_id": kb_id, "fingerprint": fingerprint},
                statuses={"pending", "running"},
                conflict_predicate=self._payloads_overlap,
                coroutine=run_reslice,
            )
        except TaskPayloadConflictError as error:
            conflicting_file_ids = sorted(set(error.task.payload.get("file_ids") or []) & set(file_ids))
            raise ResliceConflictError(f"目标文件已有正在执行的重切任务: {conflicting_file_ids}") from error
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
