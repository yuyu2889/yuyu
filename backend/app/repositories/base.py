"""
Repository 基类。

把「统计总数」和「分页查询」这类每个模块都要写的动作抽出来，
子类只需要提供「查询条件」即可。

设计约定（很重要，决定了分层是否清晰）：
- Repository 只做数据访问，**不 commit**（事务边界归 service 管）
- 需要拿到自增主键时可以 await session.flush()，flush 会发出 SQL 但不提交事务
- 不在这一层做任何业务判断（比如"状态不是 pending 就不让审核"属于业务规则）
"""
from typing import Any, Generic, List, Optional, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """所有 Repository 的基类"""

    model: Type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------- 查询 ----------

    async def get(self, obj_id: int) -> Optional[ModelT]:
        """按主键查询。session.get 会优先命中身份映射缓存，比 SELECT 更快。"""
        return await self.db.get(self.model, obj_id)

    async def count(self, *conditions: Any) -> int:
        """按条件统计总数"""
        stmt = select(func.count()).select_from(self.model)
        if conditions:
            stmt = stmt.where(*conditions)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def paginate(
        self,
        stmt: Select,
        offset: int,
        limit: int,
    ) -> Sequence[ModelT]:
        """对任意查询语句执行分页"""
        result = await self.db.execute(stmt.offset(offset).limit(limit))
        return result.scalars().all()

    # ---------- 写入 ----------
    # 注意：这些方法都只 flush，不 commit。
    # 事务的提交由 service 决定，这样一个业务操作里的多次写入
    # 要么全成功、要么全回滚，不会出现"改了一半"的中间状态。

    async def add(self, obj: ModelT) -> ModelT:
        """新增一条记录（flush 以获取自增主键）"""
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        """删除一条记录"""
        await self.db.delete(obj)
        await self.db.flush()

    async def commit(self) -> None:
        """提交事务（只有 service 应该调用）"""
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()
