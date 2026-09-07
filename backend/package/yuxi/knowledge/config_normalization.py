"""知识库 Parent-Child 与检索扩展配置的纯规范化规则。"""

from __future__ import annotations

import math
from typing import Any

BGE_M3_MODEL_IDS = frozenset({"BAAI/bge-m3", "Pro/BAAI/bge-m3"})
PARENT_CHILD_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "parent_token_num": 1000,
    "child_token_num": 200,
    "child_overlap_percent": 15,
    "separator": "\\n",
}
TOKENIZER_POLICY = "embedding_provider_or_tiktoken"
TEXT_PRESERVATION = "parsed_markdown_raw"

_QUERY_OPTION_KEYS = {
    "top_k_child",
    "top_k_parent",
    "use_vector_score_fusion",
    "dense_vector_weight",
    "sparse_vector_weight",
    "use_rrf",
}
_MISSING = object()


def is_bge_m3_embedding_model_spec(embedding_model_spec: str | None) -> bool:
    """判断模型 spec 的末段模型 ID 是否为受支持的 BGE-M3。"""
    if not isinstance(embedding_model_spec, str):
        return False

    model_id = embedding_model_spec.rsplit(":", 1)[-1].strip()
    return model_id in BGE_M3_MODEL_IDS


def normalize_knowledge_additional_params(
    additional_params: dict[str, Any] | None,
    embedding_model_spec: str | None,
) -> dict[str, Any]:
    """补齐知识库级稀疏向量与 Parent-Child 默认值并校验模型能力。"""
    params = _require_object(additional_params, "additional_params")
    normalized = dict(params)
    normalized["embedding_features"] = _normalize_embedding_features(
        params.get("embedding_features"),
        embedding_model_spec,
    )
    normalized["parent_child"] = _normalize_parent_child(params.get("parent_child"), processing=False)
    return normalized


def resolve_feature_processing_params(
    kb_additional_params: dict[str, Any] | None,
    file_processing_params: dict[str, Any] | None,
    request_params: dict[str, Any] | None,
    embedding_model_spec: str | None,
) -> dict[str, Any]:
    """按知识库、文件、任务顺序合并并规范化文件级功能参数。"""
    kb_params = _require_object(kb_additional_params, "additional_params")
    file_params = _require_object(file_processing_params, "processing_params")
    task_params = _require_object(request_params, "request_params")

    embedding_features = _merge_nested_objects(
        "embedding_features",
        (kb_params, file_params, task_params),
    )
    parent_child = _merge_nested_objects(
        "parent_child",
        (kb_params, file_params, task_params),
    )
    normalized_parent_child = _normalize_parent_child(parent_child, processing=True)

    return {
        "embedding_features": _normalize_embedding_features(embedding_features, embedding_model_spec),
        "parent_child": normalized_parent_child,
        "indexing_path": "parent_child" if normalized_parent_child["enabled"] else "single_chunk",
    }


def normalize_query_params(
    query_params: dict[str, Any] | None,
    additional_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """规范化查询 options，仅返回当前条件下实际生效的新字段。"""
    params = _require_object(query_params, "query_params")
    options = _require_object(params.get("options", {}), "query_params.options")
    normalized_options = {key: value for key, value in options.items() if key not in _QUERY_OPTION_KEYS}

    values = _validate_query_option_values(options)
    features = _require_object(additional_params, "additional_params")
    embedding_features = _require_object(features.get("embedding_features"), "embedding_features")
    parent_child = _require_object(features.get("parent_child"), "parent_child")
    sparse_enabled = _read_feature_flag(embedding_features, "bge_m3_sparse_enabled")
    parent_child_enabled = _read_feature_flag(parent_child, "enabled")
    search_mode = options.get("search_mode", "vector")
    graph_enabled = options.get("use_graph_retrieval") is True

    if parent_child_enabled:
        top_k_parent = values["top_k_parent"]
        if top_k_parent is _MISSING:
            legacy_final_top_k = options.get("final_top_k")
            top_k_parent = (
                _normalize_integer(legacy_final_top_k, "final_top_k", 1, 100) if legacy_final_top_k is not None else 10
            )

        top_k_child = values["top_k_child"]
        if top_k_child is _MISSING:
            top_k_child = max(30, top_k_parent)
        if top_k_child < top_k_parent:
            raise ValueError("top_k_child 必须大于或等于 top_k_parent")
        normalized_options["top_k_child"] = top_k_child
        normalized_options["top_k_parent"] = top_k_parent

    vector_fusion_visible = sparse_enabled and search_mode in {"vector", "hybrid"}
    if vector_fusion_visible:
        use_vector_score_fusion = values["use_vector_score_fusion"]
        if use_vector_score_fusion is _MISSING:
            use_vector_score_fusion = False
        normalized_options["use_vector_score_fusion"] = use_vector_score_fusion
        if use_vector_score_fusion:
            normalized_options["dense_vector_weight"] = values["dense_vector_weight"]
            normalized_options["sparse_vector_weight"] = values["sparse_vector_weight"]

    if search_mode == "hybrid" or graph_enabled:
        use_rrf = values["use_rrf"]
        normalized_options["use_rrf"] = False if use_rrf is _MISSING else use_rrf

    normalized = dict(params)
    normalized["options"] = normalized_options
    return normalized


def _require_object(value: object, field_name: str) -> dict[str, Any]:
    """校验可选配置值为 JSON 对象，并返回浅拷贝。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return dict(value)


def _merge_nested_objects(
    field_name: str,
    sources: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """按来源顺序合并指定嵌套对象，后出现的来源覆盖先出现的来源。"""
    merged: dict[str, Any] = {}
    for source in sources:
        if field_name not in source:
            continue
        value = source[field_name]
        if not isinstance(value, dict):
            raise ValueError(f"{field_name} 必须是对象")
        merged.update(value)
    return merged


def _normalize_embedding_features(
    raw_features: object,
    embedding_model_spec: str | None,
) -> dict[str, bool]:
    """校验稀疏向量开关，并确认当前嵌入模型具备对应能力。"""
    features = _require_object(raw_features, "embedding_features")
    sparse_enabled = _normalize_boolean(
        features.get("bge_m3_sparse_enabled", False),
        "embedding_features.bge_m3_sparse_enabled",
    )
    if sparse_enabled and not is_bge_m3_embedding_model_spec(embedding_model_spec):
        raise ValueError("只有 BGE-M3 嵌入模型可以启用 bge_m3_sparse_enabled")
    return {"bge_m3_sparse_enabled": sparse_enabled}


def _normalize_parent_child(raw_config: object, *, processing: bool) -> dict[str, Any]:
    """校验 Parent-Child 字段，并生成知识库级或文件级最终结构。"""
    config = _require_object(raw_config, "parent_child")
    enabled = _normalize_boolean(config.get("enabled", False), "parent_child.enabled")
    if not enabled:
        normalized = dict(PARENT_CHILD_DEFAULTS)
        normalized["text_preservation"] = TEXT_PRESERVATION
        if not processing:
            normalized["tokenizer_policy"] = TOKENIZER_POLICY
        return normalized

    normalized: dict[str, Any] = {
        "enabled": enabled,
        "parent_token_num": _normalize_integer(
            config.get("parent_token_num", PARENT_CHILD_DEFAULTS["parent_token_num"]),
            "parent_token_num",
            256,
            4096,
        ),
        "child_token_num": _normalize_integer(
            config.get("child_token_num", PARENT_CHILD_DEFAULTS["child_token_num"]),
            "child_token_num",
            64,
            1024,
        ),
        "child_overlap_percent": _normalize_lossless_integer(
            config.get("child_overlap_percent", PARENT_CHILD_DEFAULTS["child_overlap_percent"]),
            "child_overlap_percent",
            0,
            99,
        ),
        "separator": _normalize_separator(config.get("separator", PARENT_CHILD_DEFAULTS["separator"])),
    }
    if normalized["parent_token_num"] <= normalized["child_token_num"]:
        raise ValueError("parent_token_num 必须大于 child_token_num")

    text_preservation = config.get("text_preservation", TEXT_PRESERVATION)
    if text_preservation != TEXT_PRESERVATION:
        raise ValueError(f"parent_child.text_preservation 必须是 {TEXT_PRESERVATION}")
    normalized["text_preservation"] = TEXT_PRESERVATION

    if processing:
        tokenizer_id = config.get("tokenizer_id")
        if tokenizer_id is not None:
            if not isinstance(tokenizer_id, str) or not tokenizer_id:
                raise ValueError("parent_child.tokenizer_id 必须是非空字符串")
            normalized["tokenizer_id"] = tokenizer_id
    else:
        tokenizer_policy = config.get("tokenizer_policy", TOKENIZER_POLICY)
        if tokenizer_policy != TOKENIZER_POLICY:
            raise ValueError(f"parent_child.tokenizer_policy 必须是 {TOKENIZER_POLICY}")
        normalized["tokenizer_policy"] = TOKENIZER_POLICY

    return normalized


def _normalize_boolean(value: object, field_name: str) -> bool:
    """校验配置值为严格布尔类型，并返回原值。"""
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是布尔值")
    return value


def _normalize_integer(value: object, field_name: str, minimum: int, maximum: int) -> int:
    """校验配置值为严格整数且位于闭区间内。"""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的整数")
    return value


def _normalize_lossless_integer(value: object, field_name: str, minimum: int, maximum: int) -> int:
    """校验配置值可无损转换为整数且位于闭区间内。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的整数")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的整数")

    normalized = int(value)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的整数")
    return normalized


def _normalize_separator(value: object) -> str:
    """校验 Parent-Child 分隔符为非空字符串。"""
    if not isinstance(value, str) or not value:
        raise ValueError("parent_child.separator 必须是非空字符串")
    return value


def _normalize_number(value: object, field_name: str, minimum: float, maximum: float) -> float:
    """校验配置值为有限数字且位于闭区间内。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的数字")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} 必须是 {minimum}..{maximum} 的数字")
    return normalized


def _read_feature_flag(config: dict[str, Any], field_name: str) -> bool:
    """读取可选功能开关，缺失时按关闭处理。"""
    if field_name not in config:
        return False
    return _normalize_boolean(config[field_name], field_name)


def _validate_query_option_values(options: dict[str, Any]) -> dict[str, Any]:
    """校验所有显式查询扩展字段，包括当前条件下暂不生效的字段。"""
    values: dict[str, Any] = {
        "top_k_child": _MISSING,
        "top_k_parent": _MISSING,
        "use_vector_score_fusion": _MISSING,
        "dense_vector_weight": 0.7,
        "sparse_vector_weight": 0.3,
        "use_rrf": _MISSING,
    }
    if "top_k_child" in options:
        values["top_k_child"] = _normalize_integer(options["top_k_child"], "top_k_child", 1, 500)
    if "top_k_parent" in options:
        values["top_k_parent"] = _normalize_integer(options["top_k_parent"], "top_k_parent", 1, 100)
    if "use_vector_score_fusion" in options:
        values["use_vector_score_fusion"] = _normalize_boolean(
            options["use_vector_score_fusion"],
            "use_vector_score_fusion",
        )
    if "dense_vector_weight" in options:
        values["dense_vector_weight"] = _normalize_number(
            options["dense_vector_weight"],
            "dense_vector_weight",
            0.0,
            5.0,
        )
    if "sparse_vector_weight" in options:
        values["sparse_vector_weight"] = _normalize_number(
            options["sparse_vector_weight"],
            "sparse_vector_weight",
            0.0,
            5.0,
        )
    if values["dense_vector_weight"] == 0 and values["sparse_vector_weight"] == 0:
        raise ValueError("dense_vector_weight 与 sparse_vector_weight 不能同时为 0")
    if "use_rrf" in options:
        values["use_rrf"] = _normalize_boolean(options["use_rrf"], "use_rrf")

    explicit_child = values["top_k_child"]
    explicit_parent = values["top_k_parent"]
    if explicit_child is not _MISSING and explicit_parent is not _MISSING and explicit_child < explicit_parent:
        raise ValueError("top_k_child 必须大于或等于 top_k_parent")
    return values
