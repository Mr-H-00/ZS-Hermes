"""SQLite 逻辑单测的 UTC 回读；真实时间语义由 PostgreSQL 集成测试证明。"""

from datetime import UTC

from sqlalchemy import DateTime
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.ext.asyncio import create_async_engine


class UTCDateTime(DATETIME):
    """为 SQLite 丢失时区的带时区列恢复 UTC 标记。"""

    def result_processor(self, dialect, coltype):
        """在测试数据库的结果边界恢复声明的时区。"""
        process = super().result_processor(dialect, coltype)

        def read(value):
            parsed = process(value)
            return parsed.replace(tzinfo=UTC) if parsed is not None and self.timezone else parsed

        return read


def create_utc_sqlite_engine():
    """创建只用于内部 UTC 逻辑单测的内存数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, DateTime: UTCDateTime}
    return engine
