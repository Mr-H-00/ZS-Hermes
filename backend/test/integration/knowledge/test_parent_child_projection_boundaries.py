"""父子投影的真实 PostgreSQL 归属和代次发布边界。"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.knowledge.models import KnowledgeDocumentVersion, KnowledgeFile
from yuxi.modules.knowledge.repositories.parent_child_chunk_repository import KnowledgeParentChildChunkRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def projection_db():
    """以独立 Schema 构造两知识库及待发布父子版本。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_parent_child")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            for metadata in load_models():
                await conn.run_sync(metadata.create_all)
            await conn.execute(
                text("INSERT INTO knowledge_bases (kb_id,name,kb_type) VALUES ('kb','one','milvus'),('other','two','milvus')")
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_files (file_id,kb_id,filename,status,active_generation,building_generation,processing_job_id,processing_owner) VALUES ('file','kb','a.md','indexing',1,2,'job','worker'),('other-file','other','b.md','indexed',1,NULL,NULL,NULL)"
                )
            )
        async with manager.get_async_session_context() as session:
            session.add(
                KnowledgeDocumentVersion(
                    version_id="version",
                    kb_id="kb",
                    file_id="file",
                    generation=2,
                    doc_id="doc",
                    indexing_path="parent_child",
                    embedding_model_spec="p:BAAI/bge-m3",
                    embedding_dimension=1024,
                    chunk_preset_id="custom",
                    processing_params={},
                    status="staging",
                )
            )
        yield manager
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_version_rejects_file_from_other_kb(projection_db):
    """真实复合外键拒绝版本使用其他知识库文件。"""
    with pytest.raises(IntegrityError, match="fk_document_versions_file_scope"):
        async with projection_db.async_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_document_versions SELECT 'bad', 'other', file_id, generation, doc_id,indexing_path,embedding_model_spec,embedding_dimension,chunk_preset_id,processing_params,status,activated_at,created_at,updated_at FROM knowledge_document_versions WHERE version_id='version'"
                )
            )


async def test_parent_rejects_version_from_other_file(projection_db):
    """真实复合外键拒绝父块伪造版本归属。"""
    with pytest.raises(IntegrityError, match="fk_parent_chunks_version_scope"):
        async with projection_db.async_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_parent_chunks (parent_id,version_id,kb_id,file_id,doc_id,parent_index,parent_text,start_offset,end_offset,token_count,metadata,graph_structure_indexed,graph_indexed,graph_extraction_details,created_at,updated_at) VALUES ('bad','version','other','other-file','doc',0,'x',0,1,1,'{}',false,false,'{}',now(),now())"
                )
            )


async def test_activation_publishes_generation_with_durable_cleanup(projection_db):
    """发布事务同时更新当前代次并保存旧单层代次清理意图。"""
    async with projection_db.get_async_session_context() as session:
        await KnowledgeParentChildChunkRepository._activate_version_in_session(
            session, "version", processing_job_id="job", processing_owner="worker", chunk_count=3, token_count=10, updated_by=None
        )
    async with projection_db.get_async_session_context() as session:
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == "file"))
        assert (file.active_generation, file.building_generation, file.status, file.chunk_count) == (2, None, "indexed", 3)
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM knowledge_projection_outbox WHERE aggregate_id='file' AND generation=1 AND operation='generation_cleanup'"
                )
            )
            == 1
        )


async def test_activation_rejects_stale_building_generation(projection_db):
    """仍持有同名 owner 也不能发布已经过期的 staging 代次。"""
    async with projection_db.async_engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET building_generation=3 WHERE file_id='file'"))
    with pytest.raises(ValueError, match="staging"):
        async with projection_db.get_async_session_context() as session:
            await KnowledgeParentChildChunkRepository._activate_version_in_session(
                session, "version", processing_job_id="job", processing_owner="worker", chunk_count=3, token_count=10, updated_by=None
            )
    async with projection_db.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT active_generation FROM knowledge_files WHERE file_id='file'")) == 1
        assert await conn.scalar(text("SELECT status FROM knowledge_document_versions WHERE version_id='version'")) == "staging"


@pytest.mark.parametrize("field,value", [("version_id", "second"), ("kb_id", "other"), ("file_id", "other-file")])
async def test_child_rejects_parent_scope_mismatch(projection_db, field, value):
    """子块不能把有效父块拼接到其他版本、知识库或文件。"""
    async with projection_db.async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO knowledge_document_versions SELECT 'second', kb_id, file_id, generation, doc_id,indexing_path,embedding_model_spec,embedding_dimension,chunk_preset_id,processing_params,status,activated_at,created_at,updated_at FROM knowledge_document_versions WHERE version_id='version'"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO knowledge_parent_chunks (parent_id,version_id,kb_id,file_id,doc_id,parent_index,parent_text,start_offset,end_offset,token_count,metadata,graph_structure_indexed,graph_indexed,graph_extraction_details,created_at,updated_at) VALUES ('parent','version','kb','file','doc',0,'x',0,1,1,'{}',false,false,'{}',now(),now())"
            )
        )
    values = {"version_id": "version", "kb_id": "kb", "file_id": "file", field: value}
    with pytest.raises(IntegrityError, match="fk_child_chunks_parent_scope"):
        async with projection_db.async_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_child_chunks (child_id,parent_id,version_id,kb_id,file_id,doc_id,child_index,child_text,start_offset,end_offset,token_count,spans,metadata,created_at,updated_at) VALUES ('child','parent',:version_id,:kb_id,:file_id,'doc',0,'x',0,1,1,'[]','{}',now(),now())"
                ),
                values,
            )
