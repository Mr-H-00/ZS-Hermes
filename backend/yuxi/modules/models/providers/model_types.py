"""供应商模型的统一类型标识。"""

IMAGE_MODEL_TYPE = "image"
VALID_MODEL_TYPES = {"chat", "embedding", "rerank", IMAGE_MODEL_TYPE, "video", "audio", "seed3D"}
_LEGACY_MODEL_TYPES = {"image_generation": IMAGE_MODEL_TYPE, "3d": "seed3D"}


# 将存量配置和接口调用中的旧类型名映射到统一标识。
def normalize_model_type(model_type: str) -> str:
    """保留其他类型原值，交由各入口校验。"""
    return _LEGACY_MODEL_TYPES.get(model_type, model_type)
