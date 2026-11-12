"""知识库 Manager、调用方与类型执行器共用的内部读取模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from yuxi.modules.identity.permissions import ResourcePermission


@dataclass(frozen=True, slots=True)
class KnowledgeBaseConfig:
    """知识库类型实现执行一次操作所需的最小配置。"""

    kb_id: str
    kb_type: str
    embedding_model_spec: str | None = None
    query_params: dict[str, Any] = field(default_factory=dict)
    additional_params: dict[str, Any] = field(default_factory=dict)

    @property
    def query_options(self) -> dict[str, Any]:
        """返回 query_params 中的检索 options；缺失或非字典时回退为空字典。"""
        # query_params 直接来自持久化 JSON，options 的形状不受类型约束。
        options = self.query_params.get("options")
        return options if isinstance(options, dict) else {}


@dataclass(frozen=True, slots=True)
class KnowledgeBaseSummary:
    """知识库列表、权限过滤与资源选择共用的内部摘要。"""

    kb_id: str
    name: str
    description: str | None
    kb_type: str
    embedding_model_spec: str | None
    llm_model_spec: str | None
    query_params: dict[str, Any]
    additional_params: dict[str, Any]
    share_config: dict[str, Any] | None
    created_by: str | None
    created_at: datetime | None
    file_count: int = 0
    folder_count: int = 0
    row_count: int = 0
    total_size: int = 0
    chunk_count: int = 0
    token_count: int = 0
    pending_parse_count: int = 0
    pending_index_count: int = 0
    processing_count: int = 0
    file_status_counts: dict[str, int] = field(default_factory=dict)
    # 仅由按用户权限过滤的读取路径填充；为 None 表示未经权限过滤，can_manage 随之为 False。
    effective_permission: ResourcePermission | None = None

    @property
    def can_manage(self) -> bool:
        """返回当前调用者是否拥有管理权限。"""
        return self.effective_permission == ResourcePermission.MANAGE


@dataclass(frozen=True, slots=True)
class KnowledgeBaseDetail(KnowledgeBaseSummary):
    """知识库详情读取模型，在摘要基础上增加详情页字段。"""

    sample_questions: tuple[str, ...] = ()
    # files 以 file_id 为键；None 表示未请求文件列表，空字典表示已请求但无文件。
    files: dict[str, dict[str, Any]] | None = None
    # 文件列表按 files_page_size 截断返回首页，超出时 files_truncated 为真。
    files_truncated: bool = False
    files_page_size: int | None = None
