from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import delete, func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeChildChunk,
    KnowledgeDocumentVersion,
    KnowledgeFile,
    KnowledgeParentChunk,
)
from yuxi.utils.datetime_utils import utc_now_naive

SQL_IN_BATCH_SIZE = 10_000


class KnowledgeParentChildChunkRepository:
    """管理 Parent-Child 文档版本及父子块持久化。"""

    _parent_payload_fields = {
        "parent_id",
        "parent_index",
        "parent_text",
        "start_offset",
        "end_offset",
        "token_count",
        "graph_structure_indexed",
        "graph_indexed",
        "graph_extraction_details",
        "ent_ids",
        "tags",
        "extraction_result",
    }
    _child_payload_fields = {
        "child_id",
        "parent_id",
        "child_index",
        "child_text",
        "start_offset",
        "end_offset",
        "token_count",
        "spans",
    }

    @staticmethod
    def _iter_batches(items: list[str], batch_size: int = SQL_IN_BATCH_SIZE) -> Iterator[list[str]]:
        """按 PostgreSQL IN 查询上限切分标识列表。"""
        for index in range(0, len(items), batch_size):
            yield items[index : index + batch_size]

    async def create_staging_version(
        self,
        kb_id: str,
        file_id: str,
        doc_id: str,
        params: dict[str, Any],
        embedding_model_spec: str,
        embedding_dimension: int,
    ) -> KnowledgeDocumentVersion:
        """为指定知识文件创建尚未对检索可见的 Parent-Child 版本。"""
        indexing_path = params.get("indexing_path")
        chunk_preset_id = params.get("chunk_preset_id")
        if indexing_path != "parent_child":
            raise ValueError("Parent-Child 版本的 indexing_path 必须为 parent_child")
        if not isinstance(chunk_preset_id, str) or not chunk_preset_id:
            raise ValueError("Parent-Child 版本缺少 chunk_preset_id")

        async with pg_manager.get_async_session_context() as session:
            file_record = await session.scalar(
                select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id)
            )
            if file_record is None:
                raise ValueError("知识文件不存在或不属于指定知识库")

            version = KnowledgeDocumentVersion(
                version_id=f"docver_{uuid.uuid4().hex}",
                kb_id=kb_id,
                file_id=file_id,
                doc_id=doc_id,
                indexing_path=indexing_path,
                embedding_model_spec=embedding_model_spec,
                embedding_dimension=embedding_dimension,
                chunk_preset_id=chunk_preset_id,
                processing_params=dict(params),
                status="staging",
            )
            session.add(version)
            await session.flush()
            return version

    async def batch_insert_parent_chunks(
        self,
        version_id: str,
        parents: list[dict[str, Any]],
    ) -> list[KnowledgeParentChunk]:
        """批量写入 staging 版本父块，并从版本事实补齐文档标识。"""
        if not parents:
            return []

        async with pg_manager.get_async_session_context() as session:
            version = await session.scalar(
                select(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.version_id == version_id)
                .with_for_update()
            )
            if version is None:
                raise ValueError("文档版本不存在")
            if version.status != "staging":
                raise ValueError("只能向 staging 文档版本写入父块")

            records = []
            for parent in parents:
                metadata = parent.get("metadata", parent.get("chunk_metadata", {}))
                parent_data = {key: value for key, value in parent.items() if key in self._parent_payload_fields}
                record = KnowledgeParentChunk(
                    **parent_data,
                    version_id=version.version_id,
                    kb_id=version.kb_id,
                    file_id=version.file_id,
                    doc_id=version.doc_id,
                    chunk_metadata=metadata,
                )
                records.append(record)
            session.add_all(records)
            await session.flush()
            return records

    async def batch_insert_child_chunks(
        self,
        version_id: str,
        children: list[dict[str, Any]],
    ) -> list[KnowledgeChildChunk]:
        """批量写入 staging 版本子块，并拒绝跨版本父子映射。"""
        if not children:
            return []

        async with pg_manager.get_async_session_context() as session:
            version = await session.scalar(
                select(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.version_id == version_id)
                .with_for_update()
            )
            if version is None:
                raise ValueError("文档版本不存在")
            if version.status != "staging":
                raise ValueError("只能向 staging 文档版本写入子块")

            parent_ids = list(dict.fromkeys(str(child["parent_id"]) for child in children))
            result = await session.execute(
                select(KnowledgeParentChunk.parent_id).where(
                    KnowledgeParentChunk.version_id == version_id,
                    KnowledgeParentChunk.parent_id.in_(parent_ids),
                )
            )
            existing_parent_ids = set(result.scalars().all())
            if existing_parent_ids != set(parent_ids):
                raise ValueError("子块引用的父块不存在或不属于当前文档版本")

            records = []
            for child in children:
                metadata = child.get("metadata", child.get("chunk_metadata", {}))
                child_data = {key: value for key, value in child.items() if key in self._child_payload_fields}
                record = KnowledgeChildChunk(
                    **child_data,
                    version_id=version.version_id,
                    kb_id=version.kb_id,
                    file_id=version.file_id,
                    doc_id=version.doc_id,
                    chunk_metadata=metadata,
                )
                records.append(record)
            session.add_all(records)
            await session.flush()
            return records

    async def get_active_version(self, kb_id: str, file_id: str) -> KnowledgeDocumentVersion | None:
        """读取指定知识文件唯一的 active Parent-Child 版本。"""
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.kb_id == kb_id,
                    KnowledgeDocumentVersion.file_id == file_id,
                    KnowledgeDocumentVersion.status == "active",
                )
            )

    async def list_active_version_ids(self, kb_id: str) -> list[str]:
        """读取知识库当前所有文件的 active 版本标识。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeDocumentVersion.version_id)
                .where(
                    KnowledgeDocumentVersion.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                )
                .order_by(KnowledgeDocumentVersion.version_id.asc())
            )
            return [str(version_id) for version_id in result.scalars().all()]

    async def count_by_kb_id(self, kb_id: str) -> int:
        """统计知识库中的 ParentChunk 数量。"""
        async with pg_manager.get_async_session_context() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(KnowledgeParentChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id)
                .where(
                    KnowledgeParentChunk.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                )
            )
            return int(value or 0)

    async def count_by_file_ids(self, file_ids: list[str]) -> dict[str, int]:
        """统计指定文件当前 active 版本的父块数量。"""
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return {}

        counts: dict[str, int] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(
                    select(KnowledgeParentChunk.file_id, func.count())
                    .join(
                        KnowledgeDocumentVersion,
                        KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id,
                    )
                    .where(
                        KnowledgeParentChunk.file_id.in_(batch),
                        KnowledgeDocumentVersion.status == "active",
                    )
                    .group_by(KnowledgeParentChunk.file_id)
                )
                counts.update({str(file_id): int(count or 0) for file_id, count in result.all()})
        return counts

    async def list_parents_by_file_id(self, file_id: str) -> list[KnowledgeParentChunk]:
        """按文件读取当前 active 版本的父块。"""
        return await self.list_parents_by_file_ids([file_id])

    async def list_parents_by_file_ids(self, file_ids: list[str]) -> list[KnowledgeParentChunk]:
        """按文件批量读取当前 active 版本的父块，忽略不存在的标识。"""
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return []

        parents: list[KnowledgeParentChunk] = []
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(
                    select(KnowledgeParentChunk)
                    .join(
                        KnowledgeDocumentVersion,
                        KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id,
                    )
                    .where(
                        KnowledgeParentChunk.file_id.in_(batch),
                        KnowledgeDocumentVersion.status == "active",
                    )
                    .order_by(KnowledgeParentChunk.file_id.asc(), KnowledgeParentChunk.parent_index.asc())
                )
                parents.extend(result.scalars().all())
        return sorted(parents, key=lambda parent: (parent.file_id, parent.parent_index))

    async def sum_token_count_by_file_ids(self, file_ids: list[str]) -> dict[str, int]:
        """统计指定文件当前 active 版本的父块 token 总数。"""
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return {}

        token_counts: dict[str, int] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(
                    select(
                        KnowledgeParentChunk.file_id,
                        func.coalesce(func.sum(KnowledgeParentChunk.token_count), 0),
                    )
                    .join(
                        KnowledgeDocumentVersion,
                        KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id,
                    )
                    .where(
                        KnowledgeParentChunk.file_id.in_(batch),
                        KnowledgeDocumentVersion.status == "active",
                    )
                    .group_by(KnowledgeParentChunk.file_id)
                )
                token_counts.update({str(file_id): int(total or 0) for file_id, total in result.all()})
        return token_counts

    async def count_graph_indexed_by_kb_id(self, kb_id: str) -> int:
        """统计已完成图谱结构与向量索引的父块。"""
        async with pg_manager.get_async_session_context() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(KnowledgeParentChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id)
                .where(
                    KnowledgeParentChunk.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeParentChunk.graph_indexed.is_(True),
                )
            )
            return int(value or 0)

    async def count_graph_structure_indexed_by_kb_id(self, kb_id: str) -> int:
        """统计已写入图谱结构的父块。"""
        async with pg_manager.get_async_session_context() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(KnowledgeParentChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id)
                .where(
                    KnowledgeParentChunk.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeParentChunk.graph_structure_indexed.is_(True),
                )
            )
            return int(value or 0)

    async def list_graph_extraction_failed_samples(self, kb_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """读取 active ParentChunk 中最近的图谱抽取失败样例。"""
        status = KnowledgeParentChunk.graph_extraction_details["status"].as_string()
        async with pg_manager.get_async_session_context() as session:
            parents = list(
                (
                    await session.execute(
                        select(KnowledgeParentChunk)
                        .join(
                            KnowledgeDocumentVersion,
                            KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id,
                        )
                        .where(
                            KnowledgeParentChunk.kb_id == kb_id,
                            KnowledgeDocumentVersion.status == "active",
                            status == "failed",
                        )
                        .order_by(
                            KnowledgeParentChunk.created_at.desc(),
                            KnowledgeParentChunk.parent_id.desc(),
                        )
                        .limit(max(1, min(limit, 10)))
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "chunk_id": parent.parent_id,
                "file_id": parent.file_id,
                "chunk_index": parent.parent_index,
                "content": parent.parent_text,
                "details": parent.graph_extraction_details or {},
            }
            for parent in parents
        ]

    async def count_graph_pending_by_kb_id(self, kb_id: str) -> int:
        """统计 active 版本中尚未完成图谱索引的父块。"""
        async with pg_manager.get_async_session_context() as session:
            value = await session.scalar(
                select(func.count())
                .select_from(KnowledgeParentChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id)
                .where(
                    KnowledgeParentChunk.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeParentChunk.graph_indexed.is_not(True),
                )
            )
            return int(value or 0)

    async def count_graph_extraction_statuses_by_kb_id(self, kb_id: str) -> dict[str, int]:
        """统计 active ParentChunk 的图谱抽取状态。"""
        counts = {"pending": 0, "succeeded": 0, "failed": 0}
        status = func.coalesce(KnowledgeParentChunk.graph_extraction_details["status"].as_string(), "pending")
        async with pg_manager.get_async_session_context() as session:
            rows = (
                await session.execute(
                    select(status, func.count())
                    .select_from(KnowledgeParentChunk)
                    .join(
                        KnowledgeDocumentVersion,
                        KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id,
                    )
                    .where(
                        KnowledgeParentChunk.kb_id == kb_id,
                        KnowledgeDocumentVersion.status == "active",
                    )
                    .group_by(status)
                )
            ).all()
        for value, count in rows:
            counts[str(value)] = int(count or 0)
        return counts

    async def list_graph_pending_by_kb_id(
        self,
        kb_id: str,
        limit: int,
        *,
        after_parent_id: str = "",
    ) -> list[KnowledgeParentChunk]:
        """按稳定 parent_id 顺序读取 active 版本待处理父块。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeParentChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.version_id == KnowledgeParentChunk.version_id)
                .where(
                    KnowledgeParentChunk.kb_id == kb_id,
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeParentChunk.graph_indexed.is_not(True),
                    KnowledgeParentChunk.parent_id > (after_parent_id or ""),
                )
                .order_by(KnowledgeParentChunk.parent_id.asc())
                .limit(max(limit, 1))
            )
            return list(result.scalars().all())

    async def get_by_parent_id(self, parent_id: str) -> KnowledgeParentChunk | None:
        """按 parent_id 读取单个父块。"""
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(KnowledgeParentChunk).where(KnowledgeParentChunk.parent_id == parent_id))

    async def update_extraction_result(
        self,
        parent_id: str,
        extraction_result: dict[str, Any],
        attempt_count: int = 1,
    ) -> None:
        """保存父块图谱抽取结果。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeParentChunk)
                .where(KnowledgeParentChunk.parent_id == parent_id)
                .values(
                    extraction_result=extraction_result,
                    graph_extraction_details={"status": "succeeded", "attempt_count": attempt_count},
                )
            )

    async def mark_graph_extraction_pending(self, parent_id: str) -> None:
        """将父块抽取状态重置为待处理。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeParentChunk)
                .where(KnowledgeParentChunk.parent_id == parent_id)
                .values(graph_extraction_details={"status": "pending", "attempt_count": 0})
            )

    async def mark_graph_extraction_failed(self, parent_id: str, attempt_count: int, error: str) -> None:
        """记录父块图谱抽取失败及最后错误。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeParentChunk)
                .where(KnowledgeParentChunk.parent_id == parent_id)
                .values(
                    graph_extraction_details={
                        "status": "failed",
                        "attempt_count": attempt_count,
                        "last_error": error[:4000],
                        "last_attempt_at": utc_now_naive().isoformat(),
                    }
                )
            )

    async def mark_graph_structure_indexed(self, parent_id: str, ent_ids: list[str]) -> None:
        """标记父块的 Neo4j 结构写入已完成。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeParentChunk)
                .where(KnowledgeParentChunk.parent_id == parent_id)
                .values(graph_structure_indexed=True, ent_ids=ent_ids)
            )

    async def reset_graph_state_by_kb_id(self, kb_id: str, clear_extraction_result: bool) -> int:
        """重置 ParentChunk 图谱索引状态。"""
        values: dict[str, Any] = {"graph_structure_indexed": False, "graph_indexed": False}
        if clear_extraction_result:
            values.update(
                {
                    "extraction_result": None,
                    "graph_extraction_details": {"status": "pending", "attempt_count": 0},
                    "ent_ids": None,
                    "tags": None,
                }
            )
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(KnowledgeParentChunk).where(KnowledgeParentChunk.kb_id == kb_id).values(**values)
            )
            return int(result.rowcount or 0)

    async def activate_version(self, version_id: str) -> KnowledgeDocumentVersion:
        """原子切换 active 版本并同步知识文件的版本投影。"""
        async with pg_manager.get_async_session_context() as session:
            target_snapshot = await session.scalar(
                select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.version_id == version_id)
            )
            if target_snapshot is None:
                raise ValueError("文档版本不存在")

            file_record = await session.scalar(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == target_snapshot.kb_id,
                    KnowledgeFile.file_id == target_snapshot.file_id,
                )
                .with_for_update()
            )
            if file_record is None:
                raise ValueError("文档版本对应的知识文件不存在")

            target = await session.scalar(
                select(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.version_id == version_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None or target.status != "staging":
                raise ValueError("只有 staging 文档版本可以激活")

            now = utc_now_naive()
            await session.execute(
                update(KnowledgeDocumentVersion)
                .where(
                    KnowledgeDocumentVersion.kb_id == target.kb_id,
                    KnowledgeDocumentVersion.file_id == target.file_id,
                    KnowledgeDocumentVersion.status == "active",
                )
                .values(status="superseded", updated_at=now)
            )
            await session.flush()

            target.status = "active"
            target.activated_at = now
            target.updated_at = now
            processing_params = dict(file_record.processing_params or {})
            processing_params.update(
                {
                    "document_version_id": target.version_id,
                    "indexing_path": target.indexing_path,
                    "doc_id": target.doc_id,
                }
            )
            file_record.processing_params = processing_params
            file_record.updated_at = now
            await session.flush()
            return target

    async def delete_version(self, version_id: str) -> bool:
        """删除 staging 或 superseded 版本及其级联父子块。"""
        async with pg_manager.get_async_session_context() as session:
            version = await session.scalar(
                select(KnowledgeDocumentVersion)
                .where(KnowledgeDocumentVersion.version_id == version_id)
                .with_for_update()
            )
            if version is None:
                return False
            if version.status == "active":
                raise ValueError("active 文档版本不能通过 delete_version 删除")
            if version.status not in {"staging", "superseded"}:
                raise ValueError("只能删除 staging 或 superseded 文档版本")
            await session.delete(version)
            await session.flush()
            return True

    async def list_children_by_ids(self, child_ids: list[str]) -> list[KnowledgeChildChunk]:
        """按输入顺序批量读取子块，忽略不存在的标识。"""
        if not child_ids:
            return []

        records_by_id: dict[str, KnowledgeChildChunk] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(child_ids):
                result = await session.execute(
                    select(KnowledgeChildChunk).where(KnowledgeChildChunk.child_id.in_(batch))
                )
                records_by_id.update({record.child_id: record for record in result.scalars().all()})
        return [records_by_id[child_id] for child_id in child_ids if child_id in records_by_id]

    async def list_parents_by_ids(self, parent_ids: list[str]) -> list[KnowledgeParentChunk]:
        """按输入顺序批量读取父块，忽略不存在的标识。"""
        if not parent_ids:
            return []

        records_by_id: dict[str, KnowledgeParentChunk] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(parent_ids):
                result = await session.execute(
                    select(KnowledgeParentChunk).where(KnowledgeParentChunk.parent_id.in_(batch))
                )
                records_by_id.update({record.parent_id: record for record in result.scalars().all()})
        return [records_by_id[parent_id] for parent_id in parent_ids if parent_id in records_by_id]

    async def list_children_by_parent_ids(self, parent_ids: list[str]) -> dict[str, list[KnowledgeChildChunk]]:
        """按显式 parent_id 批量读取子块，不跨父块推断映射。"""
        normalized_ids = list(
            dict.fromkeys(str(parent_id).strip() for parent_id in parent_ids if str(parent_id).strip())
        )
        if not normalized_ids:
            return {}

        children_by_parent: dict[str, list[KnowledgeChildChunk]] = {parent_id: [] for parent_id in normalized_ids}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(
                    select(KnowledgeChildChunk)
                    .where(KnowledgeChildChunk.parent_id.in_(batch))
                    .order_by(KnowledgeChildChunk.parent_id.asc(), KnowledgeChildChunk.child_index.asc())
                )
                for child in result.scalars().all():
                    children_by_parent.setdefault(child.parent_id, []).append(child)
        return children_by_parent

    async def delete_active_by_file_id(self, kb_id: str, file_id: str) -> int:
        """显式删除指定知识文件的 active 版本及其级联父子块。"""
        async with pg_manager.get_async_session_context() as session:
            file_record = await session.scalar(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id)
                .with_for_update()
            )
            if file_record is None:
                return 0
            result = await session.execute(
                delete(KnowledgeDocumentVersion).where(
                    KnowledgeDocumentVersion.kb_id == kb_id,
                    KnowledgeDocumentVersion.file_id == file_id,
                    KnowledgeDocumentVersion.status == "active",
                )
            )
            return int(result.rowcount or 0)
