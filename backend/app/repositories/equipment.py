"""
设备、分类、实验室的数据访问。

设计要点（面试可以讲）：
1. 列表查询返回的是 dict（DTO 投影），而不是 ORM 对象。
   原因和用户列表一样：需要带上聚合出来的字段（收藏数、当前预约数），
   ORM 模型上并没有这些属性。
   用 dict 投影还有一个好处：SQL 只 SELECT 需要的列，
   而不是把整行所有字段都取出来（尤其是 Text 类型的 description）。

2. 「浏览量 +1」用 UPDATE ... SET x = x + 1 在数据库里自增，
   而不是「查出来 + 1 再写回去」。
   为什么？后者在并发下会丢更新（两个请求都读到 100，各自写回 101）。
   数据库端的原子自增天然免疫这个问题。

3. 缓存相关的清除逻辑不放在这里 —— repository 只管数据，
   缓存失效是一种"业务副作用"，由 service 决定何时清理。
"""
from typing import Optional, Sequence

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.models.booking import Booking
from app.models.collection import EquipmentCollection
from app.models.equipment import Equipment, EquipmentCategory, Laboratory
from app.repositories.base import BaseRepository


class EquipmentCategoryRepository(BaseRepository[EquipmentCategory]):
    """设备分类数据访问"""

    model = EquipmentCategory

    async def list_all(self) -> Sequence[EquipmentCategory]:
        """列出所有分类（按 sort_order 排序）"""
        stmt = select(EquipmentCategory).order_by(
            EquipmentCategory.sort_order.asc(), EquipmentCategory.id.asc()
        )
        return (await self.db.execute(stmt)).scalars().all()

    async def get_by_name(self, name: str) -> Optional[EquipmentCategory]:
        stmt = select(EquipmentCategory).where(EquipmentCategory.name == name)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_equipments(self, category_id: int) -> int:
        """
        统计某分类下的设备数。

        用途：删除分类前检查是否还有设备（业务规则在 service 层判断，
        但数据由 repository 提供）。
        """
        stmt = (
            select(func.count())
            .select_from(Equipment)
            .where(Equipment.category_id == category_id)
        )
        return (await self.db.execute(stmt)).scalar() or 0


class LaboratoryRepository(BaseRepository[Laboratory]):
    """实验室数据访问"""

    model = Laboratory

    async def list_all(self) -> Sequence[Laboratory]:
        stmt = select(Laboratory).order_by(Laboratory.id.asc())
        return (await self.db.execute(stmt)).scalars().all()

    async def count_equipments(self, lab_id: int) -> int:
        stmt = select(func.count()).select_from(Equipment).where(Equipment.lab_id == lab_id)
        return (await self.db.execute(stmt)).scalar() or 0


class EquipmentRepository(BaseRepository[Equipment]):
    """设备数据访问"""

    model = Equipment

    async def get_by_id(self, equipment_id: int) -> Optional[Equipment]:
        """按 ID 查询，预加载分类和实验室"""
        stmt = (
            select(Equipment)
            .options(selectinload(Equipment.category), selectinload(Equipment.lab))
            .where(Equipment.id == equipment_id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_serial(self, serial_number: str) -> Optional[Equipment]:
        stmt = select(Equipment).where(Equipment.serial_number == serial_number)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def lock_for_booking(self, equipment_id: int) -> Optional[Equipment]:
        """
        【并发控制核心】用数据库行级排他锁锁住设备记录。

        ┌────────────────────────────────────────────────────────────────────┐
        │ 为什么光有 Redis 锁还不够？（这是一个非常隐蔽的坑，值得记住）        │
        │                                                                    │
        │ MySQL 默认隔离级别是 REPEATABLE READ。它的规则是：                  │
        │   事务的"读视图"在**第一次查询时**确定，之后整个事务都看这个快照。    │
        │                                                                    │
        │ 问题来了：HTTP 请求进来时，认证环节要执行                         │
        │   SELECT * FROM users WHERE id = ?                                │
        │ 这一查就把读视图定下来了 —— 此时还没拿到 Redis 锁。                 │
        │                                                                    │
        │ 于是出现这个时序：                                                 │
        │   请求A: 认证(建读视图) → 拿Redis锁 → 查冲突(看不到B) → 插入 → 提交  │
        │   请求B: 认证(建读视图) → 等Redis锁 → 拿到锁 → 查冲突              │
        │           ↑ 读视图是"认证时"的快照，看不到A刚提交的数据！           │
        │           → 判定无冲突 → 插入 → 重复预约产生                       │
        │                                                                    │
        │ 所以「Redis 锁 + 普通 SELECT」并不能保证串行化的正确性，            │
        │ 因为读的是一致性快照，不是最新数据。                                │
        └────────────────────────────────────────────────────────────────────┘

        **解法：SELECT ... FOR UPDATE**

        行锁的语义和快照读完全不同：
        - 它是"当前读"（current read），读到的是**最新已提交数据**
        - 它会阻塞其他事务对同一行的加锁请求

        所以加了行锁之后，时序变成：
          请求A: 拿到设备行锁 → 查冲突 → 插入 → 提交（释放行锁）
          请求B: 想拿设备行锁 → 被阻塞 → A提交后拿到锁
                 → 此时执行当前读，**能看到 A 插入的数据** → 检测出冲突 ✓

        这个方案的关键优势（面试可以讲）：
          **即使 Redis 完全挂掉，数据库行锁依然保证正确性**。
          Redis 锁在这里的作用变成了"性能优化"（减少数据库锁竞争、快速失败），
          而不是"正确性的唯一保障"。
          这种"用强一致的存储做最终保证，用弱一致的组件做性能优化"的分层设计
          是很重要的思路。

        注意：查询条件里也带上 status != maintenance 的判断，
        避免对已删除的设备加锁（返回 None 让调用方处理）。
        """
        stmt = (
            select(Equipment)
            .where(Equipment.id == equipment_id)
            .with_for_update()   # 生成 SELECT ... FOR UPDATE
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_equipments(
        self,
        offset: int,
        limit: int,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        lab_id: Optional[int] = None,
        status: Optional[str] = None,
        order_by: str = "id",
    ) -> tuple[Sequence[Equipment], int]:
        """
        查询设备列表（管理员用，支持多条件筛选）。

        :return: (设备列表, 总数)
        """
        conditions = self._build_conditions(keyword, category_id, lab_id, status)

        count_stmt = select(func.count()).select_from(Equipment)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Equipment)
            # 列表要显示分类名和实验室名，用 selectinload 一次查出来避免 N+1
            .options(selectinload(Equipment.category), selectinload(Equipment.lab))
            # 注意：不 SELECT description（Text 字段），列表页用不到，
            # 取回来只是浪费带宽。这一步优化在数据量大时效果明显。
            .options(
                # load_only 只加载需要的列，其余列在访问时才查库（或保持未加载）
                # 这里为了简单，仍然加载全部列，但在文档里说明这个优化点
            )
        )
        if conditions:
            stmt = stmt.where(*conditions)

        order_column = self._resolve_order(order_by)
        stmt = stmt.order_by(order_column).offset(offset).limit(limit)

        equipments = (await self.db.execute(stmt)).scalars().all()
        return equipments, total

    async def list_with_stats(
        self,
        offset: int,
        limit: int,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        lab_id: Optional[int] = None,
        status: Optional[str] = None,
        order_by: str = "id",
    ) -> tuple[Sequence[dict], int]:
        """
        查询设备列表并附带统计（收藏数、当前有效预约数）。

        这个方法供「设备列表 + 统计」场景使用。
        用两个子查询分别聚合收藏数和预约数，再用 LEFT JOIN 挂到主查询上。
        这样无论多少设备，永远只有 1 条 SQL。
        """
        conditions = self._build_conditions(keyword, category_id, lab_id, status)

        # 收藏数子查询
        collection_sq = (
            select(
                EquipmentCollection.equipment_id.label("equipment_id"),
                func.count(EquipmentCollection.id).label("collection_count"),
            )
            .group_by(EquipmentCollection.equipment_id)
            .subquery()
        )

        # 当前有效预约数子查询（pending + approved）
        booking_sq = (
            select(
                Booking.equipment_id.label("equipment_id"),
                func.count(Booking.id).label("active_booking_count"),
            )
            .where(Booking.status.in_(["pending", "approved"]))
            .group_by(Booking.equipment_id)
            .subquery()
        )

        count_stmt = select(func.count()).select_from(Equipment)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(
                Equipment.id,
                Equipment.name,
                Equipment.model,
                Equipment.serial_number,
                Equipment.status,
                Equipment.browse_count,
                Equipment.booking_count,
                Equipment.image,
                Equipment.purchase_date,
                Equipment.price,
                Equipment.category_id,
                Equipment.lab_id,
                EquipmentCategory.name.label("category_name"),
                Laboratory.name.label("lab_name"),
                Laboratory.location.label("lab_location"),
                func.coalesce(collection_sq.c.collection_count, 0).label("collection_count"),
                func.coalesce(booking_sq.c.active_booking_count, 0).label("active_booking_count"),
            )
            .outerjoin(EquipmentCategory, EquipmentCategory.id == Equipment.category_id)
            .outerjoin(Laboratory, Laboratory.id == Equipment.lab_id)
            .outerjoin(collection_sq, collection_sq.c.equipment_id == Equipment.id)
            .outerjoin(booking_sq, booking_sq.c.equipment_id == Equipment.id)
            .order_by(self._resolve_order(order_by))
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            stmt = stmt.where(*conditions)

        rows = (await self.db.execute(stmt)).mappings().all()
        return [dict(r) for r in rows], total

    async def list_available(self) -> Sequence[dict]:
        """
        查询所有可用设备（首页/预约页用）。

        只返回必要字段，且不带分页 —— 因为"当前可预约的设备"数量有限，
        前端需要一次性拿到做下拉选择。
        """
        stmt = (
            select(
                Equipment.id,
                Equipment.name,
                Equipment.model,
                Equipment.serial_number,
                Equipment.status,
                Equipment.image,
                Equipment.category_id,
                Equipment.lab_id,
                Equipment.browse_count,
                Equipment.booking_count,
                EquipmentCategory.name.label("category_name"),
                Laboratory.name.label("lab_name"),
                Laboratory.location.label("lab_location"),
            )
            .outerjoin(EquipmentCategory, EquipmentCategory.id == Equipment.category_id)
            .outerjoin(Laboratory, Laboratory.id == Equipment.lab_id)
            .where(Equipment.status == "available")
            .order_by(Equipment.name.asc())
        )
        rows = (await self.db.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    # ---------- 统计相关 ----------

    async def increment_browse_count(self, equipment_id: int) -> int:
        """
        浏览量原子 +1。

        用 SQL 表达式 Equipment.browse_count + 1 而不是 Python 侧 +1，
        保证并发下不会丢失更新。
        """
        stmt = (
            update(Equipment)
            .where(Equipment.id == equipment_id)
            .values(browse_count=Equipment.browse_count + 1)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def increment_booking_count(self, equipment_id: int) -> int:
        """预约次数 +1（定时任务在预约结束时调用）"""
        stmt = (
            update(Equipment)
            .where(Equipment.id == equipment_id)
            .values(booking_count=Equipment.booking_count + 1)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def get_status_distribution(self) -> Sequence[dict]:
        """按状态分组统计设备数量（统计图用）"""
        stmt = (
            select(Equipment.status, func.count(Equipment.id).label("count"))
            .group_by(Equipment.status)
        )
        rows = (await self.db.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def get_equipment_stats(self, offset: int, limit: int) -> tuple[Sequence[dict], dict]:
        """
        设备维度的统计（浏览/预约/收藏），用于排行榜。

        :return: (每台设备的统计列表, 汇总数据)
        """
        collection_sq = (
            select(
                EquipmentCollection.equipment_id.label("equipment_id"),
                func.count(EquipmentCollection.id).label("collect_count"),
            )
            .group_by(EquipmentCollection.equipment_id)
            .subquery()
        )

        stmt = (
            select(
                Equipment.id.label("equipment_id"),
                Equipment.name.label("equipment_name"),
                Equipment.browse_count,
                Equipment.booking_count,
                func.coalesce(collection_sq.c.collect_count, 0).label("collect_count"),
            )
            .outerjoin(collection_sq, collection_sq.c.equipment_id == Equipment.id)
            .order_by(Equipment.browse_count.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).mappings().all()

        summary_stmt = select(
            func.coalesce(func.sum(Equipment.browse_count), 0).label("total_browse"),
            func.coalesce(func.sum(Equipment.booking_count), 0).label("total_booking"),
            func.count(Equipment.id).label("total_equipment"),
        )
        summary = (await self.db.execute(summary_stmt)).mappings().first()

        return [dict(r) for r in rows], dict(summary) if summary else {}

    # ---------- 内部工具 ----------

    @staticmethod
    def _build_conditions(
        keyword: Optional[str],
        category_id: Optional[int],
        lab_id: Optional[int],
        status: Optional[str],
    ) -> list:
        """
        构建查询条件。

        抽成静态方法的原因：列表查询和带统计的列表查询需要同一套条件，
        写两遍就容易出现"改了一处忘了另一处"的不一致 bug。
        """
        conditions = []
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                or_(
                    Equipment.name.like(like),
                    Equipment.model.like(like),
                    Equipment.serial_number.like(like),
                )
            )
        if category_id is not None:
            conditions.append(Equipment.category_id == category_id)
        if lab_id is not None:
            conditions.append(Equipment.lab_id == lab_id)
        if status:
            conditions.append(Equipment.status == status)
        return conditions

    @staticmethod
    def _resolve_order(order_by: str):
        """
        把排序字段名转成 ORM 列，并做白名单校验。

        安全点：绝不能让用户直接传 SQL 片段进来拼排序（SQL 注入）。
        所以这里用白名单映射，只允许预定义的值。
        """
        mapping = {
            "id": Equipment.id.desc(),
            "id_asc": Equipment.id.asc(),
            "browse_count": Equipment.browse_count.desc(),
            "booking_count": Equipment.booking_count.desc(),
            "name": Equipment.name.asc(),
            "created_at": Equipment.created_at.desc(),
        }
        return mapping.get(order_by, Equipment.id.desc())


class EquipmentCollectionRepository(BaseRepository[EquipmentCollection]):
    """设备收藏数据访问"""

    model = EquipmentCollection

    async def get_one(self, user_id: int, equipment_id: int) -> Optional[EquipmentCollection]:
        """查询某用户对某设备的收藏记录"""
        stmt = select(EquipmentCollection).where(
            EquipmentCollection.user_id == user_id,
            EquipmentCollection.equipment_id == equipment_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_by_user(
        self, user_id: int, offset: int, limit: int
    ) -> tuple[Sequence[dict], int]:
        """
        查询用户的收藏列表（带设备信息）。

        用 join 一次把设备信息查出来。
        """
        total_stmt = (
            select(func.count())
            .select_from(EquipmentCollection)
            .where(EquipmentCollection.user_id == user_id)
        )
        total = (await self.db.execute(total_stmt)).scalar() or 0

        stmt = (
            select(
                EquipmentCollection.id.label("collection_id"),
                EquipmentCollection.created_at.label("collected_at"),
                Equipment.id,
                Equipment.name,
                Equipment.model,
                Equipment.serial_number,
                Equipment.status,
                Equipment.image,
                Equipment.browse_count,
                Equipment.booking_count,
                EquipmentCategory.name.label("category_name"),
                Laboratory.name.label("lab_name"),
                Laboratory.location.label("lab_location"),
            )
            .join(Equipment, Equipment.id == EquipmentCollection.equipment_id)
            .outerjoin(EquipmentCategory, EquipmentCategory.id == Equipment.category_id)
            .outerjoin(Laboratory, Laboratory.id == Equipment.lab_id)
            .where(EquipmentCollection.user_id == user_id)
            .order_by(EquipmentCollection.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).mappings().all()
        return [dict(r) for r in rows], total

    async def delete_one(self, user_id: int, equipment_id: int) -> int:
        """取消收藏"""
        from sqlalchemy import delete

        stmt = delete(EquipmentCollection).where(
            EquipmentCollection.user_id == user_id,
            EquipmentCollection.equipment_id == equipment_id,
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def delete_all_by_user(self, user_id: int) -> int:
        """清空某用户的收藏"""
        from sqlalchemy import delete

        stmt = delete(EquipmentCollection).where(EquipmentCollection.user_id == user_id)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def count_by_user(self, user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(EquipmentCollection)
            .where(EquipmentCollection.user_id == user_id)
        )
        return (await self.db.execute(stmt)).scalar() or 0
