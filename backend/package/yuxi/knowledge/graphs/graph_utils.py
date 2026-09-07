"""图谱构建相关的纯函数工具集。

将数据变换逻辑从 MilvusGraphService 中抽离，
使 service 类专注于 I/O 和业务编排。
"""

from __future__ import annotations

from typing import Any

from yuxi.utils import hashstr


class ParentChildGraphMappingError(ValueError):
    """父子图谱映射缺少可验证标识时抛出的错误。"""


def normalize_entity_name(text: str) -> str:
    """统一实体名称：去首尾空白、小写化、压缩内部连续空白。"""
    return " ".join(text.strip().lower().split())


def compute_entity_id(kb_id: str, normalized_name: str, label: str) -> str:
    return hashstr(f"{kb_id}:{normalized_name}:{label}", length=32)


def compute_triple_id(
    kb_id: str,
    source_normalized_name: str,
    source_label: str,
    relation_type: str,
    target_normalized_name: str,
    target_label: str,
) -> str:
    return hashstr(
        f"{kb_id}:{source_normalized_name}:{source_label}:{relation_type}:{target_normalized_name}:{target_label}",
        length=32,
    )


def graph_entity_collection_name(kb_id: str) -> str:
    return f"{kb_id}_entity"


def graph_triple_collection_name(kb_id: str) -> str:
    return f"{kb_id}_triple"


def validate_parent_child_graph_mapping(parent: dict[str, Any], children: list[dict[str, Any]]) -> None:
    """校验父块和子块的显式身份，禁止用相邻记录推断关系。"""
    parent_id = str(parent.get("parent_id") or "").strip()
    if not parent_id:
        raise ParentChildGraphMappingError("Parent-Child 图谱写入缺少 parent_id")
    if not children:
        raise ParentChildGraphMappingError(f"Parent-Child 父块 {parent_id} 缺少子块")

    seen_child_ids: set[str] = set()
    for child in children:
        child_id = str(child.get("child_id") or "").strip()
        child_parent_id = str(child.get("parent_id") or "").strip()
        if not child_id:
            raise ParentChildGraphMappingError(f"Parent-Child 父块 {parent_id} 的子块缺少 child_id")
        if not child_parent_id:
            raise ParentChildGraphMappingError(f"Parent-Child 子块 {child_id} 缺少 parent_id")
        if child_parent_id != parent_id:
            raise ParentChildGraphMappingError(
                f"Parent-Child 子块 {child_id} 的 parent_id 与父块不匹配: {child_parent_id} != {parent_id}"
            )
        for field in ("file_id", "doc_id", "version_id"):
            parent_value = str(parent.get(field) or "").strip()
            child_value = str(child.get(field) or "").strip()
            if parent_value and child_value != parent_value:
                raise ParentChildGraphMappingError(
                    f"Parent-Child 子块 {child_id} 的 {field} 与父块不匹配: {child_value} != {parent_value}"
                )
        if child_id in seen_child_ids:
            raise ParentChildGraphMappingError(f"Parent-Child 子块 child_id 重复: {child_id}")
        seen_child_ids.add(child_id)


def expand_parent_scores_to_child_candidates(
    parent_scores: list[tuple[str, float]],
    children_by_parent: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """将父块图谱分数展开为带显式父子身份的子块候选。"""
    candidates: list[dict[str, Any]] = []
    for parent_id, score in parent_scores:
        normalized_parent_id = str(parent_id or "").strip()
        if not normalized_parent_id:
            raise ParentChildGraphMappingError("图谱命中缺少 parent_id，拒绝猜测父子关系")
        children = children_by_parent.get(normalized_parent_id)
        if children is None:
            raise ParentChildGraphMappingError(f"图谱命中的父块没有已知子块: {normalized_parent_id}")
        for child in children:
            child_id = str(child.get("child_id") or "").strip()
            child_parent_id = str(child.get("parent_id") or "").strip()
            if not child_id or child_parent_id != normalized_parent_id:
                raise ParentChildGraphMappingError(
                    f"图谱子块映射缺少或错配 parent_id: parent={normalized_parent_id}, child={child_id}"
                )
            candidates.append(
                {
                    "child_id": child_id,
                    "parent_id": normalized_parent_id,
                    "graph_score": float(score),
                }
            )
    return candidates


def build_graph_payload(normalized_result: dict[str, Any]) -> dict[str, Any]:
    """将抽取器产出的标准化结果转换为 Neo4j 写入所需的图结构。

    返回的 entities 已完成去重合并：同名同 label 的实体只保留一份，
    属性（attributes）取并集。
    """
    entities: list[dict[str, Any]] = []
    entity_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def add_entity(entity: dict[str, Any]) -> str:
        key = (normalize_entity_name(entity["text"]), entity.get("label") or "Entity")
        existing = entity_by_key.get(key)
        if existing is not None:
            known_attributes = {(attr["text"], attr["label"]) for attr in existing.get("attributes") or []}
            for attribute in entity.get("attributes") or []:
                attribute_key = (attribute["text"], attribute["label"])
                if attribute_key not in known_attributes:
                    existing.setdefault("attributes", []).append(attribute)
                    known_attributes.add(attribute_key)
            return existing["id"]

        graph_entity = {
            "id": f"e{len(entities) + 1}",
            "text": entity["text"],
            "label": entity.get("label") or "Entity",
            "attributes": list(entity.get("attributes") or []),
        }
        entities.append(graph_entity)
        entity_by_key[key] = graph_entity
        return graph_entity["id"]

    for entity in normalized_result["entities"]:
        add_entity(entity)

    relations = []
    for relation in normalized_result["relations"]:
        relations.append(
            {
                "source": add_entity(relation["source"]),
                "target": add_entity(relation["target"]),
                "text": relation["text"],
                "label": relation.get("label") or "RELATED_TO",
            }
        )

    return {"entities": entities, "relations": relations, "metadata": normalized_result["metadata"]}


# ─── Cypher 模板 ────────────────────────────────────────────────
# 将大段 Cypher 字符串集中管理，提升 write_chunk_graph 的可读性。


def cypher_merge_chunk(db_label: str) -> str:
    """MERGE Chunk 节点并写入元数据。"""
    return f"""
    MERGE (c:Chunk:MilvusKB:`{db_label}` {{chunk_id: $chunk_id}})
    SET c.file_id = $file_id,
        c.kb_id = $kb_id,
        c.chunk_index = $chunk_index,
        c.content_preview = $content_preview,
        c.start_char_pos = $start_char_pos,
        c.end_char_pos = $end_char_pos
    """


def cypher_merge_parent_chunk(db_label: str) -> str:
    """MERGE ParentChunk 语义主体并保存版本与文档定位。"""
    return f"""
    MERGE (p:ParentChunk:MilvusKB:`{db_label}` {{parent_id: $parent_id}})
    SET p.kb_id = $kb_id,
        p.file_id = $file_id,
        p.doc_id = $doc_id,
        p.version_id = $version_id,
        p.parent_index = $parent_index,
        p.content_preview = $content_preview,
        p.start_offset = $start_offset,
        p.end_offset = $end_offset
    """


def cypher_merge_child_chunk(db_label: str) -> str:
    """MERGE ChildChunk 从属于明确的 ParentChunk。"""
    return f"""
    MATCH (p:ParentChunk:MilvusKB:`{db_label}` {{parent_id: $parent_id}})
    MERGE (c:ChildChunk:MilvusKB:`{db_label}` {{child_id: $child_id}})
    SET c.kb_id = $kb_id,
        c.file_id = $file_id,
        c.doc_id = $doc_id,
        c.version_id = $version_id,
        c.parent_id = $parent_id,
        c.child_index = $child_index,
        c.content_preview = $content_preview,
        c.start_offset = $start_offset,
        c.end_offset = $end_offset
    MERGE (p)-[:HAS_CHILD {{kb_id: $kb_id, file_id: $file_id, version_id: $version_id,
                            parent_id: $parent_id, child_id: $child_id}}]->(c)
    """


def cypher_merge_parent_entity_mention(db_label: str) -> str:
    """MERGE ParentChunk 到实体的 MENTIONS 关系。"""
    return f"""
    MATCH (p:ParentChunk:MilvusKB:`{db_label}` {{parent_id: $parent_id}})
    MERGE (e:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $normalized_name,
        label: $entity_label
    }})
    SET e.entity_id = $entity_id,
        e.name = $name,
        e.attributes = $attributes
    MERGE (p)-[m:MENTIONS {{parent_id: $parent_id, file_id: $file_id, kb_id: $kb_id}}]->(e)
    """


def cypher_merge_entity_mention(db_label: str) -> str:
    """MERGE Entity 节点并创建 Chunk → Entity 的 MENTIONS 关系。"""
    return f"""
    MATCH (c:Chunk:MilvusKB:`{db_label}` {{chunk_id: $chunk_id}})
    MERGE (e:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $normalized_name,
        label: $entity_label
    }})
    SET e.entity_id = $entity_id,
        e.name = $name,
        e.attributes = $attributes
    MERGE (c)-[m:MENTIONS {{chunk_id: $chunk_id, file_id: $file_id, kb_id: $kb_id}}]->(e)
    """


def cypher_merge_relation(db_label: str) -> str:
    """MERGE 两个 Entity 之间的 RELATION 边。"""
    return f"""
    MATCH (source:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $source_name,
        label: $source_label
    }})
    MATCH (target:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $target_name,
        label: $target_label
    }})
    MERGE (source)-[r:RELATION {{
        kb_id: $kb_id,
        chunk_id: $chunk_id,
        source_name: $source_name,
        target_name: $target_name,
        type: $relation_type
    }}]->(target)
    SET r.triple_id = $triple_id,
        r.text = $text,
        r.file_id = $file_id,
        r.extractor_type = $extractor_type
    """


def cypher_merge_parent_relation(db_label: str) -> str:
    """MERGE Parent-Child 图谱语义主体上的实体关系。"""
    return f"""
    MATCH (source:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $source_name,
        label: $source_label
    }})
    MATCH (target:Entity:MilvusKB:`{db_label}` {{
        kb_id: $kb_id,
        normalized_name: $target_name,
        label: $target_label
    }})
    MERGE (source)-[r:RELATION {{
        kb_id: $kb_id,
        parent_id: $parent_id,
        source_name: $source_name,
        target_name: $target_name,
        type: $relation_type
    }}]->(target)
    SET r.triple_id = $triple_id,
        r.text = $text,
        r.file_id = $file_id,
        r.version_id = $version_id,
        r.extractor_type = $extractor_type
    """
