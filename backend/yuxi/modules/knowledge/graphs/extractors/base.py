"""图谱抽取器契约与抽取结果归一化。

GraphExtractor 约束抽取器的公共契约（配置校验、单分块抽取）；LLM 调用在 llm.py，
注册与创建在 factory.py。normalize_extraction_result 把不可信的模型产出清洗成
graph_utils.build_graph_payload 直接消费的稳定形状。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from yuxi.modules.knowledge.graphs.graph_utils import normalize_entity_name


class GraphExtractor(ABC):
    """图谱抽取器抽象基类：统一 options 校验与分块抽取的调用契约。"""

    # 子类声明的类型标识，随归一化结果写入 metadata.extractor_type
    extractor_type: str

    def __init__(self, options: dict[str, Any] | None = None):
        """保存抽取器配置；缺省为空 dict，必填项由 validate_options 裁决。"""
        self.options = options or {}

    @abstractmethod
    async def extract(self, text: str) -> dict[str, Any]:
        """抽取单个分块，返回未归一化的原始产出，结构错误由归一化入口统一拒绝。"""

    def validate_options(self) -> None:
        """默认不校验；具体抽取器覆写本方法，非法配置在创建时显式失败。"""


# ─── 抽取结果归一化 ──────────────────────────────────────────────

# 与 knowledge_graph_entities / knowledge_graph_triples 的列长度对齐（模型定义在
# modules/knowledge/models.py，test/unit/graphs 里有一条用例断言它们一致，避免将来漂移）。
#
# 这几个值都由模型生成、本身没有长度上限。实测出现过把整段正文当成实体名（1672 字符），
# 而 upsert_chunk_graph 是把一个分块的所有实体放进**一条多行 INSERT**：一个值超长，整批
# 被 PostgreSQL 拒绝，该分块的图谱全部丢失，且 graph_indexed 不置位——分块永远停在 pending，
# 每次重试都要再烧一次模型调用，还照样失败。
#
# 所以在落库前丢弃越界的单位（而不是抛异常：抛异常会让整个分块的抽取都失败，比丢弃更糟），
# 让损失止于它自己。
MAX_ENTITY_NAME_LENGTH = 512
MAX_ENTITY_LABEL_LENGTH = 128
MAX_RELATION_TYPE_LENGTH = 256


def normalize_extraction_result(result: Any, extractor_type: str) -> dict[str, Any]:
    """把抽取器原始产出归一化为 {"entities", "relations", "metadata"} 固定形状。

    输入是模型产出的 JSON（经 json_repair 解析，可能是任意 JSON 值），这里是不可信边界：
    结构错误（非对象、元素非对象、裸引用未知的实体、关系文本为空）抛 ValueError；
    越界（实体名/标签/关系类型超长）只丢弃受影响的实体或关系并计入 metadata.dropped_*，
    不让一个越界值拖垮整个分块。实体按（归一名, 标签）去重并合并属性，
    关系按（源, 类型, 目标）去重。
    """
    if not isinstance(result, dict):
        raise ValueError("extraction_result 必须是对象")

    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise ValueError("extraction_result.entities 和 relations 必须是数组")

    # 两阶段共享状态：先遍历 entities[] 登记 canonical 实体与裸引用表；
    # 再遍历 relations[]，内联实体对象仍经 add_entity 在此登记
    normalized_entities_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entity_refs: dict[str, dict[str, Any]] = {}
    dropped_refs: set[str] = set()
    # 按实体去重计数：同一个越界实体可能在多条关系里被反复引用，按出现次数计会虚高
    dropped_entity_keys: set[tuple[str, str]] = set()

    def add_entity(entity: Any, path: str) -> dict[str, Any] | None:
        """归一化并登记实体，返回 canonical 实体；越界不可落库时返回 None。"""
        normalized_entity = _normalize_entity(entity, path)
        if not _is_persistable_entity(normalized_entity):
            # 记下它的引用标识，引用它的关系要一并丢弃，否则会变成悬空关系
            dropped_refs.update(_entity_refs(entity, normalized_entity))
            dropped_entity_keys.add(_entity_key(normalized_entity))
            return None

        # 首个出现的实体成为 canonical，重复出现只并入属性
        existing = normalized_entities_by_key.setdefault(_entity_key(normalized_entity), normalized_entity)
        if existing is not normalized_entity:
            _merge_attributes(existing, normalized_entity)

        for ref in _entity_refs(entity, normalized_entity):
            entity_refs[ref] = existing
        return existing

    for index, entity in enumerate(entities):
        add_entity(entity, f"entities[{index}]")

    normalized_relations = []
    seen_relations = set()
    dropped_relations = 0
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError("relations 元素必须是对象")
        source = _normalize_relation_endpoint(
            relation.get("source"),
            entity_refs,
            add_entity,
            dropped_refs,
            result,
            f"relations[{index}].source",
        )
        target = _normalize_relation_endpoint(
            relation.get("target"),
            entity_refs,
            add_entity,
            dropped_refs,
            result,
            f"relations[{index}].target",
        )
        text = str(relation.get("text") or "").strip()
        if not text:
            raise ValueError("relations[].text 不能为空")
        label = str(relation.get("label") or "RELATED_TO").strip() or "RELATED_TO"
        if source is None or target is None or len(label) > MAX_RELATION_TYPE_LENGTH:
            dropped_relations += 1
            continue
        relation_key = (_entity_key(source), label, _entity_key(target))
        if relation_key in seen_relations:
            continue
        seen_relations.add(relation_key)
        normalized_relations.append(
            {
                "source": source,
                "target": target,
                "text": text,
                "label": label,
            }
        )

    metadata = dict(result.get("metadata") or {})
    metadata.setdefault("extractor_type", extractor_type)
    metadata.setdefault("schema_version", 1)
    if dropped_entity_keys or dropped_relations:
        # 丢弃必须可观察：否则只表现为图谱里少了几条，无法判断是模型没抽到还是被丢掉
        metadata["dropped_entities"] = len(dropped_entity_keys)
        metadata["dropped_relations"] = dropped_relations
    return {
        "entities": list(normalized_entities_by_key.values()),
        "relations": normalized_relations,
        "metadata": metadata,
    }


def _normalize_entity(entity: Any, path: str) -> dict[str, Any]:
    """校验并归一化单个实体：剥离空白、补默认标签、丢弃空属性；path 仅用于错误定位。"""
    if not isinstance(entity, dict):
        raise ValueError(f"{path} 必须是对象")

    text = str(entity.get("text") or "").strip()
    if not text:
        raise ValueError(f"{path}.text 不能为空")

    attributes = entity.get("attributes") or []
    if not isinstance(attributes, list):
        raise ValueError(f"{path}.attributes 必须是数组")

    normalized_attributes = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            raise ValueError(f"{path}.attributes 元素必须是对象")
        attr_text = str(attribute.get("text") or "").strip()
        if not attr_text:
            # 空属性值不构成结构错误，丢弃该属性而不是拒绝整个实体
            continue
        normalized_attributes.append(
            {
                "text": attr_text,
                "label": str(attribute.get("label") or "Attribute").strip() or "Attribute",
            }
        )

    return {
        "text": text,
        "label": str(entity.get("label") or "Entity").strip() or "Entity",
        "attributes": normalized_attributes,
    }


def _is_persistable_entity(entity: dict[str, Any]) -> bool:
    """实体名、归一化名与标签是否都能落进 knowledge_graph_entities 的列。"""
    return (
        len(entity["text"]) <= MAX_ENTITY_NAME_LENGTH
        and len(normalize_entity_name(entity["text"])) <= MAX_ENTITY_NAME_LENGTH
        and len(entity["label"]) <= MAX_ENTITY_LABEL_LENGTH
    )


def _entity_key(entity: dict[str, Any]) -> tuple[str, str]:
    """实体去重键：归一名 + 标签，仅大小写或空白差异的名称视为同一实体。"""
    return (normalize_entity_name(entity["text"]), entity["label"])


def _entity_refs(raw_entity: dict[str, Any], entity: dict[str, Any]) -> list[str]:
    """实体可被关系裸引用的标识：实体名，加上模型可能额外给出的 id。"""
    refs = [entity["text"]]
    entity_id = str(raw_entity.get("id") or "").strip()
    if entity_id:
        refs.append(entity_id)
    return refs


def _merge_attributes(target: dict[str, Any], source: dict[str, Any]) -> None:
    """按 (text, label) 去重，把重复实体的属性并入 canonical 实体。"""
    # 入参恒为 _normalize_entity 的产物，attributes 键必然存在且是列表
    known_attributes = {(attr["text"], attr["label"]) for attr in target["attributes"]}
    for attribute in source["attributes"]:
        attribute_key = (attribute["text"], attribute["label"])
        if attribute_key not in known_attributes:
            target["attributes"].append(attribute)
            known_attributes.add(attribute_key)


def _normalize_relation_endpoint(
    endpoint: Any,
    entity_refs: dict[str, dict[str, Any]],
    add_entity: Callable[[Any, str], dict[str, Any] | None],
    dropped_refs: set[str],
    result: dict[str, Any],
    path: str,
) -> dict[str, Any] | None:
    """解析关系端点：内联实体对象交 add_entity 登记去重，裸字符串按引用解析已知实体。

    返回 None 表示端点无法落库（引用名本身越界、或指向已丢弃的越界实体），调用方应丢弃
    整条关系；裸引用一个从未出现的实体是结构错误，抛 ValueError。result 仅用于错误信息
    回显原始产出。
    """
    if isinstance(endpoint, dict):
        return add_entity(endpoint, path)

    endpoint_ref = str(endpoint or "").strip()
    if len(endpoint_ref) > MAX_ENTITY_NAME_LENGTH:
        # 引用名本身就超过实体名的列长度：不可能对应任何可落库的实体，丢弃这条关系
        return None
    if endpoint_ref in dropped_refs:
        # 引用的是一个因越界被丢弃的实体：连这条关系一起丢弃，而不是报错
        return None
    entity = entity_refs.get(endpoint_ref)
    if entity is None:
        raise ValueError(
            f"relations[].source/target 必须是实体对象，或引用 entities[].text/id，未找到: {path}={endpoint_ref}, Result: {result}"
        )
    return entity
