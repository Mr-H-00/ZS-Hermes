"""图谱抽取器的注册与创建。"""

from __future__ import annotations

from typing import Any

from yuxi.modules.knowledge.graphs.extractors.base import GraphExtractor
from yuxi.modules.knowledge.graphs.extractors.llm import LLMGraphExtractor


class GraphExtractorFactory:
    """按 extractor_type 查注册表创建并校验抽取器。"""

    _registry: dict[str, type[GraphExtractor]] = {
        "llm": LLMGraphExtractor,
    }

    @classmethod
    def create(cls, extractor_type: str | None, options: dict[str, Any] | None = None) -> GraphExtractor:
        """创建指定类型的抽取器并在返回前完成配置校验。"""
        normalized_type = (extractor_type or "").lower()
        extractor_class = cls._registry.get(normalized_type)
        if not extractor_class:
            raise ValueError(f"不支持的图谱抽取器类型: {extractor_type}")
        extractor = extractor_class(options or {})
        extractor.validate_options()
        return extractor
