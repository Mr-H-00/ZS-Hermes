from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from typing import Any

from neo4j import Driver, GraphDatabase
from yuxi.infrastructure.observability.logging import logger

_SAFE_NEO4J_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_shared_neo4j_connection: Neo4jConnectionManager | None = None
_shared_neo4j_connection_lock = threading.Lock()


def safe_neo4j_label(value: str) -> str:
    """校验并返回可安全拼接进 Cypher 的标签名。"""
    if not _SAFE_NEO4J_LABEL_RE.match(value or ""):
        raise ValueError(f"非法 Neo4j 标签: {value}")
    return value


def neo4j_read(driver: Driver, cypher: str, **kwargs) -> list[dict[str, Any]]:
    """执行只读 Cypher 查询并返回结果列表。"""
    with driver.session() as session:
        result = session.run(cypher, **kwargs)
        return [record.data() for record in result]


def neo4j_write(driver: Driver, query: Callable) -> Any:
    """在写事务中执行 Cypher 操作的简写。"""
    with driver.session() as session:
        return session.execute_write(query)


class Neo4jConnectionManager:
    """构造时探测 Neo4j 连通性并持有 driver。"""

    def __init__(self):
        self.driver: Driver | None = None
        self.status = "closed"
        self._connect()

    def is_running(self) -> bool:
        """报告管理器是否处于可用或处理中状态。"""
        return self.status == "open" or self.status == "processing"

    def close(self) -> None:
        """关闭驱动并回到未连接状态。"""
        if self.driver:
            self.driver.close()
            self.driver = None
            self.status = "closed"

    def _connect(self) -> None:
        """建立驱动并执行连通性探测，失败向上抛出。"""
        if self.driver and self._is_connected():
            return

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "0123456789")

        try:
            self.driver = GraphDatabase.driver(uri, auth=(username, password))
            with self.driver.session() as session:
                session.run("RETURN 1")
            self.status = "open"
            logger.info("Successfully connected to Neo4j")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    def _is_connected(self) -> bool:
        """用轻量查询判断现有驱动是否仍可用。"""
        if not self.driver:
            return False
        try:
            with self.driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False


def get_shared_neo4j_connection() -> Neo4jConnectionManager:
    """返回进程共享连接，驱动缺失时在锁内重建。"""
    global _shared_neo4j_connection
    if _shared_neo4j_connection is None or not _shared_neo4j_connection.driver:
        with _shared_neo4j_connection_lock:
            if _shared_neo4j_connection is None or not _shared_neo4j_connection.driver:
                _shared_neo4j_connection = Neo4jConnectionManager()
    return _shared_neo4j_connection


def close_shared_neo4j_connection() -> None:
    """关闭进程共享连接。"""
    with _shared_neo4j_connection_lock:
        if _shared_neo4j_connection is not None:
            _shared_neo4j_connection.close()
