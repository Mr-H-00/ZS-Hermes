import pytest
from pydantic import ValidationError

from server.routers.knowledge_router import (
    DocumentProcessingParamsRequest,
    KnowledgeAdditionalParamsRequest,
    QueryParamsUpdateRequest,
    QueryRequest,
    ResliceDocumentsRequest,
)

pytestmark = pytest.mark.unit


def test_query_request_keeps_legacy_json_shape() -> None:
    """验证查询 DTO 保持旧版 query 与 meta 对象形状。"""
    payload = {"query": "如何部署？", "meta": {"final_top_k": 5}}

    request = QueryRequest.model_validate(payload)

    assert request.model_dump() == payload


def test_query_params_update_keeps_flat_root_shape() -> None:
    """验证查询参数更新 DTO 不引入额外包装层。"""
    payload = {"search_mode": "hybrid", "final_top_k": 5}

    request = QueryParamsUpdateRequest.model_validate(payload)

    assert request.model_dump() == payload
    assert request.root == payload


def test_knowledge_parent_child_response_can_be_submitted_for_update() -> None:
    """验证规范化响应字段可直接作为知识库更新请求再次提交。"""
    payload = {
        "chunk_preset_id": "book",
        "chunk_parser_config": {"chunk_token_num": 512},
        "embedding_features": {"bge_m3_sparse_enabled": True},
        "parent_child": {
            "enabled": True,
            "parent_token_num": 1200,
            "child_token_num": 240,
            "child_overlap_percent": 20,
            "separator": "\\n",
            "tokenizer_policy": "embedding_provider_or_tiktoken",
            "text_preservation": "parsed_markdown_raw",
        },
    }

    request = KnowledgeAdditionalParamsRequest.model_validate(payload)

    assert request.model_dump(exclude_none=True) == payload


def test_document_processing_params_keep_parent_child_overrides_and_legacy_fields() -> None:
    """验证文档任务 DTO 同时保留新增覆盖字段与旧处理字段。"""
    payload = {
        "content_type": "file",
        "chunk_preset_id": "general",
        "embedding_features": {"bge_m3_sparse_enabled": True},
        "parent_child": {
            "enabled": True,
            "child_token_num": 180,
            "tokenizer_id": "provider:test:bge-m3",
            "text_preservation": "parsed_markdown_raw",
        },
    }

    request = DocumentProcessingParamsRequest.model_validate(payload)

    assert request.model_dump(exclude_none=True) == payload


def test_reslice_request_requires_at_least_one_file() -> None:
    """验证重切片 DTO 拒绝没有目标文件的请求。"""
    with pytest.raises(ValidationError):
        ResliceDocumentsRequest.model_validate({"file_ids": [], "params": {}})
