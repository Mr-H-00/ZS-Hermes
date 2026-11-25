"""火山方舟官方目录与模型协议。"""

import re
from typing import Any
from urllib.parse import urlsplit

from yuxi.modules.models.providers.model_types import IMAGE_MODEL_TYPE

_SEED_AUDIO_ID = re.compile(r"(?:doubao-)?seed-audio(?:-|$)", re.IGNORECASE)
# 远端同名的 Seedream 4.0 中，目录只保留已发布的 20260415 版本。
RETIRED_SEEDREAM_4_ID = "doubao-seedream-4-0-250828"

# 方舟旧版目录仅给出这些模型的 ID。Seed Translation 属于豆包文本翻译系列；
# HiTem3D (hitem3d.ai) 和 Hyper3D Rodin (hyper3d.ai) 均为厂商的三维生成产品。
_LEGACY_MODEL_TYPES = {
    "doubao-seed-translation-250915": "chat",
    "hitem3d-2-0-251223": "seed3D",
    "hyper3d-gen2-260112": "seed3D",
}


# 判断配置的 URL 是否使用火山方舟官方区域域名。
def is_ark_url(url: str) -> bool:
    """按 HTTPS URL 的主机识别火山方舟。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and re.fullmatch(r"ark\.[a-z0-9-]+\.volces\.com", parts.hostname or "") is not None


# 将方舟条目投影到通用目录字段；显式分类端点可提供后备类型。
def ark_model_fields(model: dict[str, Any], fallback_type: str) -> dict[str, Any]:
    """从模型声明中提取分类、上下文和向量配置。"""
    model_id = str(model.get("id") or "").strip()
    apis = model.get("api_supported")
    apis = {name for name, enabled in apis.items() if enabled is True} if isinstance(apis, dict) else set()
    modalities = model.get("modalities")
    modalities = modalities if isinstance(modalities, dict) else {}
    outputs = _strings(modalities.get("output_modalities"))
    fields: dict[str, Any] = {
        "type": (
            "audio"
            if _SEED_AUDIO_ID.match(model_id)
            else _model_type(apis, set(_strings(model.get("task_type"))), model.get("domain"), outputs, fallback_type)
        ),
        "input_modalities": _strings(modalities.get("input_modalities")),
        "output_modalities": outputs,
    }
    if fields["type"] == "unknown":
        fields["type"] = _LEGACY_MODEL_TYPES.get(model_id, "unknown")
    limits = model.get("token_limits")
    limits = limits if isinstance(limits, dict) else {}
    fields["context_length"] = _positive_integer(model.get("context_window"), limits.get("context_window"))
    fields["max_completion_tokens"] = _positive_integer(limits.get("max_output_token_length"), model.get("max_output_tokens"))
    if fields["type"] == "embedding":
        fields["dimension"] = _positive_integer(model.get("dimension"))
        if "embeddings/multimodal" in apis and "embeddings" not in apis:
            fields["embedding_protocol"] = "ark_multimodal"
    return {key: value for key, value in fields.items() if value is not None}


# 根据官方条目逐级选择真实分类，缺失时保留调用端点已知的类型。
def _model_type(apis: set[str], tasks: set[str], domain: Any, outputs: list[str], fallback: str) -> str:
    """优先使用执行协议，再读取任务、领域或输出模态。"""
    if "rerank" in apis:
        return "rerank"
    if apis & {"embeddings", "embeddings/multimodal"}:
        return "embedding"
    if "images/generations" in apis:
        return IMAGE_MODEL_TYPE
    # 支持语音输入的对话模型仍能输出文本，只有独立语音接口、生成任务或音频输出才属于 audio。
    if apis & {"audio/speech", "audio/transcriptions", "audio/translations"} or "audio" in outputs:
        return "audio"
    if tasks & {"text_to_speech", "speech_synthesis", "audio_generation"} or domain in ("AudioGeneration", "SpeechSynthesis"):
        return "audio"
    if "chat/completions" in apis:
        return "chat"
    if "rerank" in tasks or domain == "Rerank":
        return "rerank"
    if tasks & {"text_embedding", "image_embedding"} or domain == "Embedding" or outputs == ["embedding"]:
        return "embedding"
    if tasks & {"text_to_image", "image_to_image"} or domain == "ImageGeneration" or outputs == ["image"]:
        return IMAGE_MODEL_TYPE
    if "text_generation" in tasks or domain in ("LLM", "VLM") or outputs == ["text"]:
        return "chat"
    if (
        domain == "VideoGeneration"
        or outputs == ["video"]
        or tasks & {"text_to_video", "image_to_video", "multimodal_to_video", "video_editing", "video_extension"}
    ):
        return "video"
    if domain == "3DGeneration" or outputs == ["3D"] or "image_to_3D" in tasks:
        return "seed3D"
    return fallback


# 过滤目录 JSON 中不符合协议的列表项。
def _strings(value: Any) -> list[str]:
    """提取文本列表，用于解析任务与模态。"""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


# 在同一数值的多个官方字段中选择有效的正整数。
def _positive_integer(*values: Any) -> int | None:
    """提取上游声明的正整数，排除 JSON 布尔值。"""
    return next((number for number in values if type(number) is int and number > 0), None)
