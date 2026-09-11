from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.config_normalization import (
    is_bge_m3_embedding_model_spec,
    normalize_knowledge_additional_params,
    normalize_query_params,
)
from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.knowledge.utils.kb_utils import resolve_processing_params


@pytest.mark.parametrize(
    "model_spec",
    [
        "BAAI/bge-m3",
        "Pro/BAAI/bge-m3",
        "siliconflow-cn:BAAI/bge-m3",
        "siliconflow-cn:Pro/BAAI/bge-m3",
    ],
)
def test_bge_m3_model_spec_recognizes_supported_ids(model_spec: str) -> None:
    """验证无前缀和带供应商前缀的 BGE-M3 模型标识均被识别。"""
    assert is_bge_m3_embedding_model_spec(model_spec) is True


@pytest.mark.parametrize(
    "model_spec",
    [None, "", "BAAI/bge-m3-v2", "siliconflow-cn:BAAI/bge-large-zh-v1.5"],
)
def test_bge_m3_model_spec_rejects_other_ids(model_spec: str | None) -> None:
    """验证缺失或非 BGE-M3 模型标识不具备稀疏向量能力。"""
    assert is_bge_m3_embedding_model_spec(model_spec) is False


def test_additional_params_reject_sparse_for_non_bge_m3() -> None:
    """验证非 BGE-M3 模型不能开启模型稀疏向量。"""
    with pytest.raises(ValueError, match="BGE-M3"):
        normalize_knowledge_additional_params(
            {"embedding_features": {"bge_m3_sparse_enabled": True}},
            "provider:BAAI/bge-large-zh-v1.5",
        )


@pytest.mark.parametrize(
    ("parent_child", "error_match"),
    [
        ({"enabled": True, "parent_token_num": 255}, "parent_token_num"),
        ({"enabled": True, "parent_token_num": 4097}, "parent_token_num"),
        ({"enabled": True, "parent_token_num": 1000.0}, "parent_token_num"),
        ({"enabled": True, "child_token_num": 63}, "child_token_num"),
        ({"enabled": True, "child_token_num": 1025}, "child_token_num"),
        ({"enabled": True, "parent_token_num": 256, "child_token_num": 256}, "大于"),
        ({"enabled": True, "child_overlap_percent": 15.5}, "child_overlap_percent"),
        ({"enabled": True, "child_overlap_percent": 100}, "child_overlap_percent"),
        ({"enabled": True, "separator": ""}, "separator"),
    ],
)
def test_additional_params_reject_invalid_parent_child_values(
    parent_child: dict,
    error_match: str,
) -> None:
    """验证 Parent-Child 的类型、范围和父子关系约束会直接拒绝非法值。"""
    with pytest.raises(ValueError, match=error_match):
        normalize_knowledge_additional_params(
            {"parent_child": parent_child},
            "provider:BAAI/bge-m3",
        )


def test_additional_params_fill_defaults_without_dropping_legacy_fields() -> None:
    """验证旧配置可读取，并补齐新配置的稳定默认值。"""
    normalized = normalize_knowledge_additional_params(
        {
            "chunk_preset_id": "general",
            "chunk_parser_config": {"chunk_token_num": 300},
            "auto_generate_questions": False,
        },
        "provider:BAAI/bge-large-zh-v1.5",
    )

    assert normalized == {
        "chunk_preset_id": "general",
        "chunk_parser_config": {"chunk_token_num": 300},
        "auto_generate_questions": False,
        "embedding_features": {"bge_m3_sparse_enabled": False},
        "parent_child": {
            "enabled": False,
            "parent_token_num": 1000,
            "child_token_num": 200,
            "child_overlap_percent": 15,
            "separator": "\\n",
            "tokenizer_policy": "embedding_provider_or_tiktoken",
            "text_preservation": "parsed_markdown_raw",
        },
    }


def test_disabled_parent_child_ignores_inactive_invalid_fields() -> None:
    """验证关闭 Parent-Child 后隐藏参数不会阻断旧单层配置。"""
    normalized = normalize_knowledge_additional_params(
        {
            "parent_child": {
                "enabled": False,
                "parent_token_num": 100,
                "child_token_num": 200,
                "child_overlap_percent": 120,
                "separator": "",
            }
        },
        "provider:text-embedding-3-small",
    )

    assert normalized["parent_child"] == {
        "enabled": False,
        "parent_token_num": 1000,
        "child_token_num": 200,
        "child_overlap_percent": 15,
        "separator": "\\n",
        "text_preservation": "parsed_markdown_raw",
        "tokenizer_policy": "embedding_provider_or_tiktoken",
    }


def test_additional_params_accept_lossless_overlap_number() -> None:
    """验证可无损转换的重叠比例数字会被规范化为整数。"""
    normalized = normalize_knowledge_additional_params(
        {"parent_child": {"enabled": True, "child_overlap_percent": 15.0}},
        "provider:BAAI/bge-m3",
    )

    assert normalized["parent_child"]["child_overlap_percent"] == 15


def test_processing_params_use_request_over_file_over_knowledge_base() -> None:
    """验证任务级、文件级、知识库级参数按固定优先级逐层覆盖。"""
    resolved = resolve_processing_params(
        kb_additional_params={
            "embedding_features": {"bge_m3_sparse_enabled": False},
            "parent_child": {
                "enabled": False,
                "parent_token_num": 1200,
                "child_token_num": 180,
                "child_overlap_percent": 10,
                "separator": "\\n",
            },
        },
        file_processing_params={
            "embedding_features": {"bge_m3_sparse_enabled": False},
            "parent_child": {"enabled": True, "child_token_num": 220},
        },
        request_params={
            "embedding_features": {"bge_m3_sparse_enabled": True},
            "parent_child": {"parent_token_num": 1500, "child_overlap_percent": 20},
        },
        embedding_model_spec="siliconflow-cn:Pro/BAAI/bge-m3",
    )

    assert resolved["embedding_features"] == {"bge_m3_sparse_enabled": True}
    assert resolved["parent_child"] == {
        "enabled": True,
        "parent_token_num": 1500,
        "child_token_num": 220,
        "child_overlap_percent": 20,
        "separator": "\\n",
        "text_preservation": "parsed_markdown_raw",
    }
    assert resolved["indexing_path"] == "parent_child"


def test_processing_params_reject_task_sparse_for_non_bge_m3() -> None:
    """验证任务级覆盖不能绕过 BGE-M3 稀疏向量能力校验。"""
    with pytest.raises(ValueError, match="BGE-M3"):
        resolve_processing_params(
            kb_additional_params={},
            file_processing_params={},
            request_params={"embedding_features": {"bge_m3_sparse_enabled": True}},
            embedding_model_spec="provider:BAAI/bge-large-zh-v1.5",
        )


def test_query_params_reject_zero_vector_fusion_weights() -> None:
    """验证向量融合原始权重不能同时为零。"""
    with pytest.raises(ValueError, match="不能同时为 0"):
        normalize_query_params(
            {
                "options": {
                    "search_mode": "hybrid",
                    "use_vector_score_fusion": True,
                    "dense_vector_weight": 0,
                    "sparse_vector_weight": 0,
                }
            },
            {
                "embedding_features": {"bge_m3_sparse_enabled": True},
                "parent_child": {"enabled": False},
            },
        )


def test_query_params_reject_parent_limit_inversion() -> None:
    """验证父块返回数不能超过子块召回数。"""
    with pytest.raises(ValueError, match="top_k_child"):
        normalize_query_params(
            {"options": {"top_k_child": 5, "top_k_parent": 10}},
            {
                "embedding_features": {"bge_m3_sparse_enabled": False},
                "parent_child": {"enabled": True},
            },
        )


def test_query_params_expose_only_effective_new_fields() -> None:
    """验证新查询字段按 Parent-Child、稀疏向量和检索模式条件生效。"""
    normalized = normalize_query_params(
        {
            "options": {
                "search_mode": "hybrid",
                "final_top_k": 8,
                "use_graph_retrieval": False,
                "use_vector_score_fusion": True,
                "dense_vector_weight": 2,
                "sparse_vector_weight": 1,
                "use_rrf": True,
                "legacy_option": "kept",
            }
        },
        {
            "embedding_features": {"bge_m3_sparse_enabled": True},
            "parent_child": {"enabled": True},
        },
    )

    assert normalized == {
        "options": {
            "search_mode": "hybrid",
            "final_top_k": 8,
            "use_graph_retrieval": False,
            "legacy_option": "kept",
            "top_k_child": 30,
            "top_k_parent": 8,
            "use_vector_score_fusion": True,
            "dense_vector_weight": 2.0,
            "sparse_vector_weight": 1.0,
            "use_rrf": True,
        }
    }


def test_query_params_hide_inactive_fields_but_validate_supplied_values() -> None:
    """验证未满足条件的新字段不会泄露，但显式非法值仍会被拒绝。"""
    normalized = normalize_query_params(
        {
            "options": {
                "search_mode": "keyword",
                "top_k_child": 40,
                "top_k_parent": 20,
                "use_vector_score_fusion": True,
                "dense_vector_weight": 1,
                "sparse_vector_weight": 1,
                "use_rrf": True,
            }
        },
        {
            "embedding_features": {"bge_m3_sparse_enabled": False},
            "parent_child": {"enabled": False},
        },
    )

    assert normalized == {"options": {"search_mode": "keyword"}}

    with pytest.raises(ValueError, match="dense_vector_weight"):
        normalize_query_params(
            {"options": {"search_mode": "keyword", "dense_vector_weight": 5.1}},
            {
                "embedding_features": {"bge_m3_sparse_enabled": False},
                "parent_child": {"enabled": False},
            },
        )


def test_milvus_retrieval_config_defines_parent_child_fusion_and_rrf_fields() -> None:
    """验证 Milvus 参数定义发布新增字段、边界和条件显示规则。"""
    from yuxi.knowledge.implementations.milvus import MilvusKB

    config = MilvusKB.__new__(MilvusKB).get_query_params_config("kb_1")
    options = {option["key"]: option for option in config["options"]}

    assert options["top_k_child"]["default"] == 30
    assert options["top_k_child"]["max"] == 500
    assert options["top_k_parent"]["default"] == 10
    assert options["dense_vector_weight"]["depend_on"] == ("use_vector_score_fusion", True)
    assert options["sparse_vector_weight"]["max"] == 5.0
    assert options["use_rrf"]["visible_when"] == {"any": [{"search_mode": "hybrid"}, {"use_graph_retrieval": True}]}


@pytest.mark.asyncio
async def test_manager_returns_normalized_effective_config(monkeypatch, tmp_path) -> None:
    """验证 Manager 从缓存回读后只返回最终生效的知识库和查询配置。"""
    manager = KnowledgeBaseManager(str(tmp_path))
    executor = SimpleNamespace(
        apply_chunk_defaults=True,
        normalize_additional_params=lambda params: dict(params or {}),
        get_default_query_params=lambda _kb_id: {"options": {}},
    )
    snapshot = {
        "kb_id": "kb_1",
        "kb_type": "milvus",
        "embedding_model_spec": "provider:BAAI/bge-m3",
        "additional_params": {
            "embedding_features": {"bge_m3_sparse_enabled": True},
            "parent_child": {"enabled": True},
        },
        "query_params": {
            "options": {
                "search_mode": "vector",
                "final_top_k": 7,
                "use_rrf": True,
                "dense_vector_weight": 4,
            }
        },
    }

    async def get_cached_config(kb_id: str) -> dict:
        """返回测试构造的缓存配置。"""
        assert kb_id == "kb_1"
        return snapshot

    monkeypatch.setattr("yuxi.knowledge.manager.get_cached_kb_config", get_cached_config)
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, kb_type: kb_type == "milvus"),
    )
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", AsyncMock(return_value=executor))

    config = await manager.get_kb_config("kb_1")

    assert config.additional_params["parent_child"]["parent_token_num"] == 1000
    assert config.query_options == {
        "search_mode": "vector",
        "final_top_k": 7,
        "top_k_child": 30,
        "top_k_parent": 7,
        "use_vector_score_fusion": False,
    }
