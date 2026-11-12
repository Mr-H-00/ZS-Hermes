"""业务和知识模型使用独立的 SQLAlchemy metadata。"""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

BusinessBase = declarative_base()
KnowledgeBase = declarative_base()
Base = BusinessBase
JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
