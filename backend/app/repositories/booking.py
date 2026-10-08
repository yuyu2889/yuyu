"""
预约数据访问。

设计要点（面试可以讲）：

1. **冲突查询为什么用「两个不等式」而不是拼接 datetime 比较？**
   原项目用 MySQL 的 `str_to_date(concat(booking_date, ' ', end_time))`
   在 SQL 里现场拼时间，这有两个严重问题：
     a) 函数作用于列 → **索引失效**。数据库无法用 (equipment_id, booking_date, status)
        索引来加速，只能全表扫描。
     b) 数据库方言绑定 → 换成 PostgreSQL/SQLite 就跑不了（sqlite 没有 str_to_date）。
   V2 的做法：因为已经限定"同一设备 + 同一天"，所以只需要比较 time 类型
   就行，直接用两个不等式，索引能正常命中，且跨数据库通用。

2. **分页查询用 joinedload 还是 selectinload？**
   预约列表要显示"设备名"和"预约人姓名"，是一对一关系（每个预约对应
   一个设备、一个用户），用 joinedload 走 LEFT JOIN 一次查回来最合适。
   如果用 selectinload，会变成「查预约 → 再查用户 → 再查设备」共 3 条 SQL。

   注意：joinedload + limit 会有坑 —— 如果 join 的是"一对多"关系，
   limit 限制的是 join 后的行数而不是主表行数，结果会不对。
   本项目 join 的都是"多对一"（预约 → 用户/设备），不会膨胀，所以安全。
"""
from datetime import date, time
from typing import Optional, Sequence

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import joinedload, selectinload

from app.core.enums import BOOKING_ACTIVE_STATUSES, BookingStatus
from app.models.booking import Booking
from app.models.equipment import Equipment
from app.repositories.base import BaseRepository

# 有效占用状态的值列表（用于 SQL 的 IN 查询）
ACTIVE_STATUS_VALUES = [s.value for s in BOOKING_ACTIVE_STATUSES]


class BookingRepository(BaseRepository[Booking]):
    """预约数据访问"""

    model = Booking

    # ====================== 查询单个 ======================

    async def get_with_relations(self, booking_id: int) -> Optional[Booking]:
        """
        按 ID 查询，并预加载用户、设备、审核人。

        为什么要预加载？因为响应里需要 equipment.name / user.real_name，
        不预加载就会触发惰性加载 —— 在异步环境里会抛 MissingGreenlet 错误。
        """
        stmt = (
            select(Booking)
            .options(
                joinedload(Booking.user),
                joinedload(Booking.equipment),
            )
            .where(Booking.id == booking_id)
        )
        result = await self.db.execute(stmt)
        return result.unique().scalar_one_or_none()

    # ====================== 冲突检测（核心） ======================

    async def find_user_conflicts(
        self,
        user_id: int,
        booking_date: date,
        start_time: time,
        end_time: time,
        exclude_booking_id: Optional[int] = None,
    ) -> Sequence[Booking]:
        """
        查询该用户在指定时段内的冲突预约。

        ┌──────────────────────────────────────────────────────────────┐
        │ 冲突条件： NOT (existing_end <= new_start OR               │
        │                existing_start >= new_end)                  │
        │         = existing_end > new_start AND                     │
        │           existing_start < new_end                         │
        └──────────────────────────────────────────────────────────────┘

        ⚠️⚠️ 这里有本项目最隐蔽的一个坑，务必看懂 ⚠️⚠️

        **为什么这个查询必须加 with_for_update()？**

        MySQL 默认隔离级别是 REPEATABLE READ。它的规则是：
          事务的"一致性读视图（consistent read view）"在**第一次查询时**建立，
          之后该事务里的普通 SELECT 都读这个快照，看不到其他事务后来的提交。

        而一个 HTTP 请求的处理流程是：
          1. 认证：SELECT user FROM users WHERE id=?   ← 读视图在这一刻建立！
          2. 拿 Redis 锁
          3. 拿数据库行锁（SELECT ... FOR UPDATE）
          4. 查冲突 ← 普通 SELECT，读的是第 1 步的快照
          5. 插入

        所以即使第 3 步已经把我们串行化了，第 4 步依然看不到
        "上一个请求在后面提交的数据"，于是判定无冲突 → 重复预约。

        我在调试这个 bug 时做了个最小实验来验证：
          3 个并发事务 → 都被 FOR UPDATE 正确串行化（日志显示依次获得锁）
          → 但每个事务查到的冲突数都是 0 → 全部插入成功 → 3 条重复数据
        这个实验证明问题不在锁，而在"读到了旧快照"。

        **解法：把冲突查询也变成"当前读"**

        加 with_for_update() 后，该 SELECT 变成当前读（current read），
        会读取"最新已提交"的数据，同时还会对扫描到的行加间隙锁（gap lock），
        进一步阻止其他事务插入满足条件的记录。这正是我们需要的语义。

        这个坑的普遍意义（面试可以讲）：
          **在 REPEATABLE READ 下，"先查后插"这种模式即使在应用层加了锁，
           也必须保证那个"查"是当前读，否则一致性快照会让检测失效。**
          很多人只知道加锁，不知道还要处理快照问题。
        """
        conditions = [
            Booking.user_id == user_id,
            Booking.booking_date == booking_date,
            Booking.status.in_(ACTIVE_STATUS_VALUES),
            # 区间重叠：existing.end > new.start AND existing.start < new.end
            Booking.end_time > start_time,
            Booking.start_time < end_time,
        ]
        if exclude_booking_id is not None:
            conditions.append(Booking.id != exclude_booking_id)

        stmt = (
            select(Booking)
            .options(joinedload(Booking.equipment))
            .where(and_(*conditions))
            .order_by(Booking.start_time.asc())
            # ★ 关键：当前读，绕过 REPEATABLE READ 的一致性快照
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        return result.unique().scalars().all()

    async def find_equipment_conflicts(
        self,
        equipment_id: int,
        booking_date: date,
        start_time: time,
        end_time: time,
        exclude_booking_id: Optional[int] = None,
    ) -> Sequence[Booking]:
        """
        查询该设备在指定时段内的冲突预约。

        与用户冲突的逻辑完全一致，只是过滤维度从 user_id 换成 equipment_id。
        索引 idx_booking_equip_date_status 正好服务这个查询：
        (equipment_id, booking_date, status) 三个等值条件 + 两个时间范围条件。

        同样需要 with_for_update()（理由见 find_user_conflicts 的详细说明）。
        """
        conditions = [
            Booking.equipment_id == equipment_id,
            Booking.booking_date == booking_date,
            Booking.status.in_(ACTIVE_STATUS_VALUES),
            Booking.end_time > start_time,
            Booking.start_time < end_time,
        ]
        if exclude_booking_id is not None:
            conditions.append(Booking.id != exclude_booking_id)

        stmt = (
            select(Booking)
            .options(joinedload(Booking.user))
            .where(and_(*conditions))
            .order_by(Booking.start_time.asc())
            # ★ 关键：当前读
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        return result.unique().scalars().all()

    async def find_user_conflicts_readonly(
        self,
        user_id: int,
        booking_date: date,
        start_time: time,
        end_time: time,
    ) -> Sequence[Booking]:
        """
        只读版本的冲突查询（不加锁）。

        用途：**冲突预检接口**。预检只是给用户提前反馈，
        不需要（也不应该）加锁 —— 加锁会阻塞其他用户的正常预约，
        而预检结果的准确性要求本来就不高（提交时会再校验一次）。

        这是"按场景选择锁策略"的一个例子：
        写操作必须严格，读操作可以放宽。
        """
        conditions = [
            Booking.user_id == user_id,
            Booking.booking_date == booking_date,
            Booking.status.in_(ACTIVE_STATUS_VALUES),
            Booking.end_time > start_time,
            Booking.start_time < end_time,
        ]
        stmt = (
            select(Booking)
            .options(joinedload(Booking.equipment))
            .where(and_(*conditions))
            .order_by(Booking.start_time.asc())
        )
        result = await self.db.execute(stmt)
        return result.unique().scalars().all()

    async def find_equipment_conflicts_readonly(
        self,
        equipment_id: int,
        booking_date: date,
        start_time: time,
        end_time: time,
    ) -> Sequence[Booking]:
        """只读版本的设备冲突查询（供冲突预检使用，不加锁）"""
        conditions = [
            Booking.equipment_id == equipment_id,
            Booking.booking_date == booking_date,
            Booking.status.in_(ACTIVE_STATUS_VALUES),
            Booking.end_time > start_time,
            Booking.start_time < end_time,
        ]
        stmt = (
            select(Booking)
            .where(and_(*conditions))
            .order_by(Booking.start_time.asc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_booked_slots(
        self, equipment_id: int, booking_date: date
    ) -> Sequence[tuple[time, time, str]]:
        """
        获取某设备某天所有已占用的时段。

        用途：
        1. 冲突预检接口返回给前端画时间轴
        2. 设备详情页展示"今天哪些时段已被占用"

        安全点：只返回时间段和状态，**不返回预约人信息**
        （否则任何人都能看到"谁在什么时候用什么设备"，
        这是隐私泄露。管理员接口才返回完整信息）。
        """
        stmt = (
            select(Booking.start_time, Booking.end_time, Booking.status)
            .where(
                Booking.equipment_id == equipment_id,
                Booking.booking_date == booking_date,
                Booking.status.in_(ACTIVE_STATUS_VALUES),
            )
            .order_by(Booking.start_time.asc())
        )
        result = await self.db.execute(stmt)
        return result.all()

    # ====================== 列表查询 ======================

    async def list_by_user(
        self,
        user_id: int,
        offset: int,
        limit: int,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> tuple[Sequence[Booking], int]:
        """
        查询某用户的预约列表。

        :return: (预约列表, 总数)
        """
        conditions = [Booking.user_id == user_id]
        if status:
            conditions.append(Booking.status == status)
        if date_from:
            conditions.append(Booking.booking_date >= date_from)
        if date_to:
            conditions.append(Booking.booking_date <= date_to)

        # 总数
        count_stmt = select(func.count()).select_from(Booking).where(and_(*conditions))
        total = (await self.db.execute(count_stmt)).scalar() or 0

        # 数据：用 joinedload 一次把设备和用户带回来（都是多对一，不会膨胀）
        stmt = (
            select(Booking)
            .options(
                joinedload(Booking.equipment),
                joinedload(Booking.user),
            )
            .where(and_(*conditions))
            # 排序：先按预约日期倒序（最近的在前），同一天按开始时间倒序
            .order_by(Booking.booking_date.desc(), Booking.start_time.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.unique().scalars().all(), total

    async def list_all(
        self,
        offset: int,
        limit: int,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        equipment_id: Optional[int] = None,
        keyword: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> tuple[Sequence[Booking], int]:
        """
        管理员查询所有预约（多条件筛选）。

        keyword 会匹配预约人姓名、设备名、使用目的。
        """
        conditions = []
        if status:
            conditions.append(Booking.status == status)
        if user_id:
            conditions.append(Booking.user_id == user_id)
        if equipment_id:
            conditions.append(Booking.equipment_id == equipment_id)
        if date_from:
            conditions.append(Booking.booking_date >= date_from)
        if date_to:
            conditions.append(Booking.booking_date <= date_to)

        # 关键字搜索需要 join 用户表和设备表
        stmt_base = select(Booking)
        count_base = select(func.count()).select_from(Booking)

        # 关键字搜索需要跨表（用户表、设备表），用 EXISTS 子查询实现。
        # 为什么不用 JOIN？因为 JOIN 之后主表行会重复（一个预约 join 一个用户
        # 是一对一没问题，但如果以后加上一对多的关联就会重复计数）。
        # EXISTS 子查询天然不会影响主表行数，语义也更清晰。
        if keyword:
            from app.models.user import User

            like = f"%{keyword}%"

            # 该预约的预约人姓名/用户名匹配关键字
            user_match = (
                select(Booking.id)
                .join(User, User.id == Booking.user_id)
                .where(
                    Booking.id == Booking.id,
                    or_(User.real_name.like(like), User.username.like(like)),
                )
                .correlate(Booking)
                .exists()
            )
            # 该预约的设备名/序列号匹配关键字
            equipment_match = (
                select(Booking.id)
                .join(Equipment, Equipment.id == Booking.equipment_id)
                .where(
                    Booking.id == Booking.id,
                    or_(Equipment.name.like(like), Equipment.serial_number.like(like)),
                )
                .correlate(Booking)
                .exists()
            )
            # 或者使用目的本身匹配
            conditions.append(
                or_(user_match, equipment_match, Booking.purpose.like(like))
            )

        if conditions:
            stmt_base = stmt_base.where(and_(*conditions))
            count_base = count_base.where(and_(*conditions))

        total = (await self.db.execute(count_base)).scalar() or 0

        stmt = (
            stmt_base.options(
                joinedload(Booking.equipment),
                joinedload(Booking.user),
            )
            .order_by(Booking.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.unique().scalars().all(), total

    # ====================== 批量状态操作 ======================

    async def update_status(
        self,
        booking_id: int,
        new_status: BookingStatus,
        auditor_id: Optional[int] = None,
        audit_note: Optional[str] = None,
    ) -> int:
        """
        更新预约状态。

        用 UPDATE 语句而不是"查出对象改属性再 commit"，
        好处是只需要一条 SQL，且并发下更安全。
        """
        from app.core.security import utc_now

        values = {"status": new_status.value}
        if auditor_id is not None:
            values["audited_by"] = auditor_id
            values["audited_at"] = utc_now()
        if audit_note is not None:
            values["audit_note"] = audit_note

        stmt = update(Booking).where(Booking.id == booking_id).values(**values)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    # ====================== 定时任务专用查询 ======================

    async def list_in_progress(
        self, status: str = BookingStatus.APPROVED.value
    ) -> Sequence[Booking]:
        """
        查询所有"已通过"的预约。

        定时任务用它来扫描需要处理状态的预约。
        注意：这里**没有**加日期过滤。原项目为了优化性能只查今日/明日，
        但那样会漏掉"过期很久但没被扫到"的预约（比如服务停了一周再启动）。
        V2 的策略是：先查全部 approved（数量天然有限，因为只有未来的预约
        才可能是 approved），在 Python 侧判断时间。
        如果数据量大到需要优化，正确做法是加日期下限过滤：
            WHERE status='approved' AND booking_date >= CURDATE() - INTERVAL 7 DAY
        这样既能利用索引，又不会漏掉需要处理的近期数据。
        """
        stmt = select(Booking).where(Booking.status == status)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def list_pending_count(self) -> int:
        """统计待审核数量（首页卡片用）"""
        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(Booking.status == BookingStatus.PENDING.value)
        )
        return (await self.db.execute(stmt)).scalar() or 0

    # ====================== 统计 ======================

    async def count_by_status(self) -> Sequence[tuple[str, int]]:
        """按状态分组统计（统计图用）"""
        stmt = select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        result = await self.db.execute(stmt)
        return result.all()

    async def count_active_by_equipment(self, equipment_id: int) -> int:
        """
        统计设备当前的有效预约数。

        用途：删除设备前的检查、设备详情页显示"有 N 个待处理预约"。
        """
        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.equipment_id == equipment_id,
                Booking.status.in_(ACTIVE_STATUS_VALUES),
            )
        )
        return (await self.db.execute(stmt)).scalar() or 0

    async def list_completed_between(
        self, start_date: date, end_date: date
    ) -> Sequence[tuple[date, int]]:
        """
        统计指定日期范围内已完成的预约数量（按创建日期分组）。

        用于"近 7 天预约趋势"折线图。
        注意这里用的是 created_at（预约创建时间）而不是 booking_date（使用日期）——
        趋势图想表达的是"用户每天提交了多少预约"，所以应该用创建时间。
        原项目文档里写的也是 created_at，但注释说的是"预约日期"，语义含糊。
        """
        stmt = (
            select(
                func.date(Booking.created_at).label("d"),
                func.count(Booking.id).label("c"),
            )
            .where(
                func.date(Booking.created_at) >= start_date,
                func.date(Booking.created_at) <= end_date,
                Booking.status == BookingStatus.COMPLETED.value,
            )
            .group_by(func.date(Booking.created_at))
        )
        result = await self.db.execute(stmt)
        return result.all()

    async def count_by_date(self, target_date: date) -> int:
        """统计某天的预约数（不看状态，用于"今日预约"指标）"""
        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(Booking.booking_date == target_date)
        )
        return (await self.db.execute(stmt)).scalar() or 0
