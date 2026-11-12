"""知识库运行时装配：导入期注册执行器类型并构建进程级单例。

KnowledgeBaseFactory 不依赖任何具体实现，本模块是其唯一注册点；
消费者拿到 knowledge_base 时注册已全部完成，工厂首次使用前即就绪。
"""

import os

from yuxi.infrastructure.runtime_settings import get_runtime_dir
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.implementations.dify import DifyKB
from yuxi.modules.knowledge.implementations.feishu import FeishuKB
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.implementations.notion import NotionKB
from yuxi.modules.knowledge.manager import KnowledgeBaseManager

# 注册顺序决定 get_available_types 的类型展示顺序（注册表按字典插入序遍历）。
KnowledgeBaseFactory.register(MilvusKB)
KnowledgeBaseFactory.register(FeishuKB)
KnowledgeBaseFactory.register(DifyKB)
KnowledgeBaseFactory.register(NotionKB)

knowledge_base = KnowledgeBaseManager(os.path.join(get_runtime_dir(), "knowledge_base_data"))
