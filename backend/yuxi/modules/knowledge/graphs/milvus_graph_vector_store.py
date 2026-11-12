"""图谱实体与三元组向量的 Milvus 存取适配。

集合 schema、字段顺序与 metric 属于持久化契约，公共装配复用 storage/milvus.py；
图编排归 graphs/service.py。同步 Milvus 查询经 run_milvus_query_io 有界卸载执行，
写路径用 await_io 包装线程，取消时等待 I/O 收尾。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from pymilvus import (
    Collection,
    DataType,
    FieldSchema,
    connections,
    db,
    utility,
)

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.graphs.graph_utils import graph_entity_collection_name, graph_triple_collection_name
from yuxi.modules.knowledge.storage.milvus import (
    VECTOR_METRIC_TYPE,
    content_fields,
    create_collection,
    embedding_field,
    get_embedding_function,
)
from yuxi.modules.knowledge.storage.offload import run_milvus_query_io
from yuxi.modules.models.providers.cache import ModelInfo, model_cache
from yuxi.shared.hashing import hashstr

# 模型信息未上报维度时创建向量字段的兜底维度；改变会使新建集合与存量集合不兼容。
DEFAULT_EMBEDDING_DIMENSION = 1024


class MilvusGraphVectorStore:
    """图谱向量的 Milvus 集合读写；每个知识库按实体/三元组各建一个集合。"""

    def __init__(self) -> None:
        self.milvus_token = os.getenv("MILVUS_TOKEN") or ""
        self.milvus_uri = os.getenv("MILVUS_URI") or "http://localhost:19530"
        self.milvus_db = os.getenv("MILVUS_DB") or "yuxi"
        # 连接别名由 URI 派生，同一 Milvus 地址的多个实例共享连接。
        self.connection_alias = f"milvus_graph_{hashstr(self.milvus_uri, 6)}"
        self._init_connection()

    def _init_connection(self) -> None:
        """建立共享连接并切换到目标数据库；切换失败显式传播，不回退默认库。"""
        if not connections.has_connection(self.connection_alias):
            connections.connect(alias=self.connection_alias, uri=self.milvus_uri, token=self.milvus_token)
        if self.milvus_db not in db.list_database(using=self.connection_alias):
            db.create_database(self.milvus_db, using=self.connection_alias)
        db.using_database(self.milvus_db, using=self.connection_alias)

    # ─── 写入：批量嵌入与幂等 upsert ──────────────────────────────

    async def upsert_graph_records(
        self,
        *,
        kb_id: str,
        embedding_model_spec: str,
        record_type: str,
        records: list[dict[str, Any]],
    ) -> None:
        """批量嵌入记录内容并幂等 upsert 到对应类型的集合。"""
        if not records:
            return
        embedding_info = model_cache.get_model_info(embedding_model_spec)
        if not embedding_info or embedding_info.model_type != "embedding":
            raise ValueError(f"Unsupported embedding model: {embedding_model_spec}")

        # 一次解析记录类型的写路径；集合装配字段与列式 upsert 顺序互相对应。
        if record_type == "entity":
            ensure_collection, write_records = self._get_or_create_entity_collection, self._upsert_entities
        elif record_type == "triple":
            ensure_collection, write_records = self._get_or_create_triple_collection, self._upsert_triples
        else:
            raise ValueError(f"Unsupported graph vector record type: {record_type}")

        collection = await await_io(asyncio.to_thread(ensure_collection, kb_id, embedding_info))
        embed = get_embedding_function(embedding_model_spec)
        embeddings = await embed([record["content"] for record in records])
        await await_io(asyncio.to_thread(write_records, collection, records, embeddings))

    # ─── 检索：实体与三元组相似度查询 ─────────────────────────────

    async def search_entities(
        self,
        *,
        kb_id: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """语义检索实体集合，返回 id、content 与相似度 score。"""
        return await self._search_graph_collection(
            collection_name=graph_entity_collection_name(kb_id),
            query_text=query_text,
            embedding_model_spec=embedding_model_spec,
            top_k=top_k,
            output_fields=["id", "content"],
        )

    async def search_triples(
        self,
        *,
        kb_id: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """语义检索三元组集合，额外返回 source_id/target_id 端点。"""
        return await self._search_graph_collection(
            collection_name=graph_triple_collection_name(kb_id),
            query_text=query_text,
            embedding_model_spec=embedding_model_spec,
            top_k=top_k,
            output_fields=["id", "content", "source_id", "target_id"],
        )

    # ─── 删除：向量记录与集合清理 ─────────────────────────────────

    async def delete_graph_records(self, kb_id: str, *, entity_ids: list[str], triple_ids: list[str]) -> None:
        """并发删除实体与三元组向量记录。"""
        tasks = []
        if entity_ids:
            tasks.append(asyncio.to_thread(self._delete_ids, graph_entity_collection_name(kb_id), entity_ids))
        if triple_ids:
            tasks.append(asyncio.to_thread(self._delete_ids, graph_triple_collection_name(kb_id), triple_ids))
        if tasks:
            # return_exceptions 等两侧 SDK 线程全部结束后才抛首个失败；
            # 先抛会让调用方提前释放投影锁，另一侧仍在晚删。
            results = await await_io(asyncio.gather(*tasks, return_exceptions=True))
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def drop_graph_collections(self, kb_id: str) -> None:
        """删除库级实体与三元组集合；失败记录日志后向上传播。"""
        for collection_name in [graph_entity_collection_name(kb_id), graph_triple_collection_name(kb_id)]:
            try:
                if utility.has_collection(collection_name, using=self.connection_alias):
                    utility.drop_collection(collection_name, using=self.connection_alias)
                    logger.info(f"Dropped Milvus graph collection {collection_name}")
            except Exception as exc:
                logger.error(f"Failed to drop Milvus graph collection {collection_name}: {exc}")
                raise

    # ─── 内部辅助：检索执行 ───────────────────────────────────────

    async def _search_graph_collection(
        self,
        *,
        collection_name: str,
        query_text: str,
        embedding_model_spec: str,
        top_k: int,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        """嵌入查询文本后在指定集合上做相似度检索；集合不存在说明库尚无图谱向量，返回空。"""
        has_collection = await run_milvus_query_io(utility.has_collection, collection_name, using=self.connection_alias)
        if not has_collection:
            return []
        if top_k <= 0:
            return []
        embed = get_embedding_function(embedding_model_spec)
        query_embedding = await embed([query_text])
        return await run_milvus_query_io(
            self._search_graph_collection_sync,
            collection_name,
            query_embedding,
            top_k,
            output_fields,
        )

    def _search_graph_collection_sync(
        self,
        collection_name: str,
        query_embedding: list[list[float]],
        top_k: int,
        output_fields: list[str],
    ) -> list[dict[str, Any]]:
        """在卸载线程内加载集合、执行检索并投影结果。"""
        collection = Collection(name=collection_name, using=self.connection_alias)
        collection.load()
        results = collection.search(
            data=query_embedding,
            anns_field="embedding",
            param={"metric_type": VECTOR_METRIC_TYPE, "params": {"nprobe": 10}},
            limit=top_k,
            output_fields=output_fields,
        )
        if not results or not results[0]:
            return []

        records = []
        for hit in results[0]:
            entity = hit.entity
            record = {field: entity.get(field) for field in output_fields}
            record["score"] = float(hit.distance or 0.0)
            records.append(record)
        return records

    # ─── 内部辅助：集合装配（字段顺序属于持久化契约） ─────────────

    def _get_or_create_entity_collection(self, kb_id: str, embedding_info: ModelInfo) -> Collection:
        """装配实体集合字段：共享主键、正文与向量字段。"""
        collection_name = graph_entity_collection_name(kb_id)
        fields = [*content_fields(), embedding_field(embedding_info.dimension or DEFAULT_EMBEDDING_DIMENSION)]
        return self._get_or_create_collection(collection_name, fields, embedding_info)

    def _get_or_create_triple_collection(self, kb_id: str, embedding_info: ModelInfo) -> Collection:
        """装配三元组集合字段：在实体字段上追加两端实体 ID。"""
        collection_name = graph_triple_collection_name(kb_id)
        fields = [
            *content_fields(),
            # 端点 ID 与共享主键 id 同宽，插入位置不得调整（列式写入按此顺序）。
            FieldSchema(name="source_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="target_id", dtype=DataType.VARCHAR, max_length=100),
            embedding_field(embedding_info.dimension or DEFAULT_EMBEDDING_DIMENSION),
        ]
        return self._get_or_create_collection(collection_name, fields, embedding_info)

    def _get_or_create_collection(self, collection_name: str, fields: list[FieldSchema], embedding_info: ModelInfo) -> Collection:
        """存在即复用，缺失则按共享契约创建集合与索引。"""
        if utility.has_collection(collection_name, using=self.connection_alias):
            return Collection(name=collection_name, using=self.connection_alias)

        return create_collection(
            collection_name,
            fields=fields,
            description=f"Knowledge graph collection {collection_name} using {embedding_info.model_id}",
            using=self.connection_alias,
        )

    # ─── 内部辅助：线程内写入与删除 ───────────────────────────────

    def _upsert_entities(self, collection: Collection, records: list[dict[str, Any]], embeddings: list[list[float]]) -> None:
        """按实体集合字段顺序列式幂等写入。"""
        collection.upsert(
            [
                [record["id"] for record in records],
                [record["content"] for record in records],
                embeddings,
            ]
        )

    def _upsert_triples(self, collection: Collection, records: list[dict[str, Any]], embeddings: list[list[float]]) -> None:
        """按三元组集合字段顺序列式幂等写入。"""
        collection.upsert(
            [
                [record["id"] for record in records],
                [record["content"] for record in records],
                [record["source_id"] for record in records],
                [record["target_id"] for record in records],
                embeddings,
            ]
        )

    def _delete_ids(self, collection_name: str, ids: list[str]) -> None:
        """分批删除集合中的记录；集合不存在视为已清理，直接跳过。"""
        if not utility.has_collection(collection_name, using=self.connection_alias):
            return
        collection = Collection(name=collection_name, using=self.connection_alias)
        # 删除表达式按固定 ID 数量分批下发，控制单条表达式长度。
        batch_size = 1000
        for start in range(0, len(ids), batch_size):
            batch = ids[start : start + batch_size]
            quoted_ids = ", ".join(f'"{item}"' for item in batch)
            collection.delete(expr=f"id in [{quoted_ids}]")
