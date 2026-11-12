"""WPS 配置与用户连接的 PostgreSQL 访问边界。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import WPSCloudConfig, WPSConnection


class CloudDriveRepository:
    """在调用方事务内读写 WPS 配置和本人连接。"""

    # 用异步数据库会话初始化仓储，事务提交由服务层拥有。
    def __init__(self, db: AsyncSession):
        """绑定当前请求的数据库会话。"""
        self.db = db

    # 按锁模式读取共享配置，返回唯一配置记录或 None。
    async def get_config(self, *, lock: bool = False, shared: bool = False) -> WPSCloudConfig | None:
        """读取共享配置；连接操作使用共享锁与管理员更新互斥。"""
        query = select(WPSCloudConfig).where(WPSCloudConfig.id == 1).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update(read=shared)
        return await self.db.scalar(query)

    # 用 UID 限定连接可见性，锁定时避免并发刷新令牌。
    async def get_connection(self, uid: str, *, lock: bool = False) -> WPSConnection | None:
        """只读取指定用户的连接。"""
        query = select(WPSConnection).where(WPSConnection.uid == uid).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        return await self.db.scalar(query)

    # 为 UID 幂等建立连接占位，返回已加行锁的记录。
    async def ensure_connection(self, uid: str) -> WPSConnection:
        """并发首次授权共用同一用户连接行。"""
        await self.db.execute(insert(WPSConnection).values(uid=uid).on_conflict_do_nothing(index_elements=["uid"]))
        return await self.get_connection(uid, lock=True)
