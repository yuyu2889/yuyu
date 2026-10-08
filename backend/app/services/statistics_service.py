"""
统计服务。

设计要点（面试可以讲）：

1. **聚合接口（BFF 模式）**
   首页需要 8 个数字，原项目发了 5 个请求去凑。
   V2 用一个 `/statistics/dashboard` 接口一次返回。
   减少网络往返是前端性能优化最有效的手段之一 ——
   因为浏览器并发请求数有限（HTTP/1.1 下同域名 6 个），
   而且每个请求都有握手、头部开销。

2. **"活跃用户"的准确定义**
   见 schemas/statistics.py 的注释。核心是：
   用 last_used_at（真实访问行为）而不是 expires_at（Token 是否过期）。

3. **统计查询要"一次查完"**
   原项目统计 7 天趋势是"循环 7 天，每天发一条 SQL"，
   也就是 7 次数据库往返。V2 用 `GROUP BY 日期` 一次查回来，
   缺的日期在 Python 侧补 0。
   这是"把循环计算下推到数据库"的典型优化。

4. **用 CASE WHEN 做条件聚合，而不是发多条 SQL**
   `SUM(CASE WHEN status='active' THEN 1 ELSE 0 END)`
   一次查询就能算出多个指标。
   注意这里用标准 SQL 的 case()，而不是 MySQL 专有的 IF() ——
   保证跨数据库可移植。

5. **统计结果要不要缓存？**
   统计查询通常比较重（多个聚合、join）。
   但这些数字变化频繁（有人预约就变），缓存容易脏。
   V2 的选择是：**不缓存**，但用"单次聚合查询"把开销压到最低。
   如果要缓存，正确做法是"短 TTL + 写操作时主动失效"，
   而不是长 TTL（会看到过期数据）。
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    BOOKING_ACTIVE_STATUS_VALUES,
    BookingStatus,
    EquipmentStatus,
    RoleCode,
)
from app.core.security import utc_now
from app.models.booking import Booking
from app.models.collection import EquipmentCollection
from app.models.equipment import Equipment, EquipmentCategory, Laboratory
from app.models.user import Role, User, UserRole, UserToken

logger = logging.getLogger(__name__)

# 状态英文 → 中文 映射
EQUIPMENT_STATUS_NAMES = {
    EquipmentStatus.AVAILABLE.value: "可用",
    EquipmentStatus.BUSY.value: "使用中",
    EquipmentStatus.MAINTENANCE.value: "维护中",
}

BOOKING_STATUS_NAMES = {
    BookingStatus.PENDING.value: "待审核",
    BookingStatus.APPROVED.value: "已通过",
    BookingStatus.REJECTED.value: "已拒绝",
    BookingStatus.CANCELLED.value: "已取消",
    BookingStatus.COMPLETED.value: "已完成",
}


def _count_if(condition) -> object:
    """
    生成 `SUM(CASE WHEN condition THEN 1 ELSE 0 END)` 表达式。

    用途：一次查询里算出多个计数指标，避免发多条 SQL。

    为什么不用 MySQL 的 IF() 函数？
    因为那是 MySQL 专有语法，换成 PostgreSQL/SQLite 就跑不了。
    标准 SQL 的 CASE WHEN 更通用。
    """
    return func.sum(case((condition, 1), else_=0))


class StatisticsService:
    """统计业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ==========================================================================
    #  用户统计
    # ==========================================================================

    async def get_user_statistics(self) -> dict:
        """用户统计（一条 SQL 算出多个指标）"""
        week_ago = datetime.now() - timedelta(days=7)

        stmt = select(
            func.count(User.id).label("total"),
            _count_if(User.status == "active").label("active"),
            _count_if(User.status == "disabled").label("disabled"),
            _count_if(User.created_at >= week_ago).label("new_7d"),
        )
        row = (await self.db.execute(stmt)).mappings().first()

        # 当前有效会话数
        now = utc_now()
        online = (
            await self.db.execute(
                select(func.count())
                .select_from(UserToken)
                .where(UserToken.expires_at > now, UserToken.is_revoked == False)  # noqa: E712
            )
        ).scalar() or 0

        # 近 30 天活跃用户（基于 last_used_at，这才是"活跃"的准确口径）
        thirty_days_ago = utc_now() - timedelta(days=30)
        active_30d = (
            await self.db.execute(
                select(func.count(func.distinct(UserToken.user_id))).where(
                    UserToken.last_used_at >= thirty_days_ago
                )
            )
        ).scalar() or 0

        # 管理员数量
        admin_count = (
            await self.db.execute(
                select(func.count(func.distinct(UserRole.user_id)))
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == RoleCode.ADMIN.value)
            )
        ).scalar() or 0

        return {
            "total_users": int(row["total"] or 0),
            "active_users_30d": int(active_30d),
            "online_sessions": int(online),
            "disabled_users": int(row["disabled"] or 0),
            "new_users_7d": int(row["new_7d"] or 0),
            "admin_count": int(admin_count),
            "statistics_time": datetime.now(),
        }

    # ==========================================================================
    #  设备统计
    # ==========================================================================

    async def get_equipment_statistics(
        self, limit: int = 10, order_by: str = "browse_count"
    ) -> dict:
        """
        设备统计与排行。

        用两个子查询分别聚合收藏数和有效预约数，再 LEFT JOIN 到主查询，
        这样无论多少设备都只有 **1 条 SQL**（而不是 N+1）。
        """
        collect_sq = (
            select(
                EquipmentCollection.equipment_id.label("eid"),
                func.count(EquipmentCollection.id).label("cnt"),
            )
            .group_by(EquipmentCollection.equipment_id)
            .subquery()
        )

        # 「当前有效预约」= 该设备下状态为 pending 或 approved 的预约条数。
        #
        # 这里曾经写成 BookingStatus.PENDING.value, BookingStatus.APPROVED.value，
        # 属于「枚举用对了但没复用常量」—— 同一个业务概念在三个文件里各写一遍。
        # 现在统一从 core/enums.py 的 BOOKING_ACTIVE_STATUS_VALUES 导入，
        # 保证和「设备列表的预约数」「冲突检测的状态范围」永远一致。
        active_sq = (
            select(
                Booking.equipment_id.label("eid"),
                func.count(Booking.id).label("cnt"),
            )
            .where(Booking.status.in_(BOOKING_ACTIVE_STATUS_VALUES))
            .group_by(Booking.equipment_id)
            .subquery()
        )

        # 排序字段白名单（防 SQL 注入）
        order_map = {
            "browse_count": Equipment.browse_count.desc(),
            "booking_count": Equipment.booking_count.desc(),
            "collect_count": func.coalesce(collect_sq.c.cnt, 0).desc(),
        }
        order_clause = order_map.get(order_by, Equipment.browse_count.desc())

        stmt = (
            select(
                Equipment.id.label("equipment_id"),
                Equipment.name.label("equipment_name"),
                EquipmentCategory.name.label("category_name"),
                Laboratory.name.label("lab_name"),
                Equipment.browse_count,
                Equipment.booking_count,
                func.coalesce(collect_sq.c.cnt, 0).label("collect_count"),
                func.coalesce(active_sq.c.cnt, 0).label("active_booking_count"),
            )
            .outerjoin(EquipmentCategory, EquipmentCategory.id == Equipment.category_id)
            .outerjoin(Laboratory, Laboratory.id == Equipment.lab_id)
            .outerjoin(collect_sq, collect_sq.c.eid == Equipment.id)
            .outerjoin(active_sq, active_sq.c.eid == Equipment.id)
            .order_by(order_clause)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).mappings().all()

        summary = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(Equipment.browse_count), 0).label("browse"),
                    func.coalesce(func.sum(Equipment.booking_count), 0).label("booking"),
                    func.count(Equipment.id).label("equipment"),
                )
            )
        ).mappings().first()

        total_collect = (
            await self.db.execute(select(func.count()).select_from(EquipmentCollection))
        ).scalar() or 0

        return {
            "equipment_ranking": [self._normalize_row(dict(r)) for r in rows],
            "total_browse_count": int(summary["browse"] or 0),
            "total_booking_count": int(summary["booking"] or 0),
            "total_collect_count": int(total_collect),
            "total_equipment_count": int(summary["equipment"] or 0),
            "statistics_time": datetime.now(),
        }

    async def get_equipment_status_distribution(self) -> dict:
        """
        设备状态分布（适配饼图）。

        关键点：**缺失的状态要补 0**。
        如果数据库里没有 busy 状态的设备，GROUP BY 结果里就不会有 busy 这一行，
        前端饼图就会少一块，视觉上不对劲。
        所以要在 Python 侧补齐所有预定义状态。
        """
        stmt = select(Equipment.status, func.count(Equipment.id)).group_by(Equipment.status)
        rows = (await self.db.execute(stmt)).all()

        counts: Dict[str, int] = {status: 0 for status in EQUIPMENT_STATUS_NAMES}
        for status, count in rows:
            counts[status] = counts.get(status, 0) + count

        total = sum(counts.values())
        distribution = []
        for status, name in EQUIPMENT_STATUS_NAMES.items():
            count = counts.get(status, 0)
            distribution.append({
                "status": status,
                "status_name": name,
                "count": count,
                "percentage": round(count / total * 100, 2) if total > 0 else 0.0,
            })

        return {
            "status_distribution": distribution,
            "total_equipment_count": total,
            "statistics_time": datetime.now(),
        }

    # ==========================================================================
    #  预约统计
    # ==========================================================================

    async def get_booking_statistics(self) -> dict:
        """预约统计概览（含各状态分布）"""
        stmt = select(Booking.status, func.count(Booking.id)).group_by(Booking.status)
        rows = (await self.db.execute(stmt)).all()

        counts: Dict[str, int] = {status: 0 for status in BOOKING_STATUS_NAMES}
        for status, count in rows:
            counts[status] = counts.get(status, 0) + count

        total = sum(counts.values())
        distribution = []
        for status, name in BOOKING_STATUS_NAMES.items():
            count = counts.get(status, 0)
            distribution.append({
                "status": status,
                "status_name": name,
                "count": count,
                "percentage": round(count / total * 100, 2) if total > 0 else 0.0,
            })

        today_count = (
            await self.db.execute(
                select(func.count())
                .select_from(Booking)
                .where(Booking.booking_date == date.today())
            )
        ).scalar() or 0

        return {
            "total_bookings": total,
            "pending_count": counts[BookingStatus.PENDING.value],
            "approved_count": counts[BookingStatus.APPROVED.value],
            "completed_count": counts[BookingStatus.COMPLETED.value],
            "rejected_count": counts[BookingStatus.REJECTED.value],
            "cancelled_count": counts[BookingStatus.CANCELLED.value],
            "today_bookings": int(today_count),
            "status_distribution": distribution,
            "statistics_time": datetime.now(),
        }

    async def get_weekly_booking_trend(self, days: int = 7, include_today: bool = True) -> dict:
        """
        近 N 天预约趋势（适配折线图）。

        **关键优化**：原项目是"循环 7 天，每天发一条 COUNT 查询"，
        也就是 7 次数据库往返。V2 用 GROUP BY 一次查回来，
        缺的日期在 Python 侧补 0。

        口径说明（重要，容易搞混）：
        这里统计的是**按创建日期**分组的预约数，
        即"用户每天提交了多少条预约申请"，反映的是"需求/活跃度变化"。
        如果要统计"设备使用量"，应该按 booking_date 分组且只算 completed。
        两种口径差别很大，代码里必须写清楚，否则数据看板会误导人。
        """
        today = date.today()
        end_date = today if include_today else today - timedelta(days=1)
        start_date = end_date - timedelta(days=days - 1)

        # 用 CAST(... AS DATE) 而不是 MySQL 的 DATE() 函数，
        # 这样在 PostgreSQL 上用 CAST(... AS DATE) 也能跑（通用性更好）。
        from sqlalchemy import Date, cast

        date_col = cast(Booking.created_at, Date)

        stmt = (
            select(date_col.label("d"), func.count(Booking.id).label("c"))
            .where(date_col >= start_date, date_col <= end_date)
            .group_by(date_col)
        )
        rows = (await self.db.execute(stmt)).all()

        count_map = {}
        for d, c in rows:
            key = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            count_map[key] = int(c)

        # 生成连续日期并补 0（折线图必须是连续的，不能断）
        daily = []
        for i in range(days):
            current = start_date + timedelta(days=i)
            key = current.strftime("%Y-%m-%d")
            daily.append({
                "date": key,
                "booking_count": count_map.get(key, 0),
                "label": current.strftime("%m-%d"),
            })

        values = [d["booking_count"] for d in daily]
        total = sum(values)

        return {
            "daily_bookings": daily,
            "total_weekly_booking": total,
            "avg_daily_booking": round(total / days, 2) if days > 0 else 0.0,
            "max_daily_booking": max(values) if values else 0,
        }

    # ==========================================================================
    #  仪表盘总览（聚合接口）
    # ==========================================================================

    async def get_dashboard_overview(self, user: User) -> dict:
        """
        首页仪表盘总览。

        把首页需要的所有数字一次返回，避免前端发 5 个请求。

        权限说明：普通用户和管理员看到的指标不同 ——
        普通用户只关心自己的预约，管理员还要看全局数据。
        这里用 is_admin 分支，**避免给普通用户泄露全局统计**
        （比如总用户数、全平台待审核数）。
        """
        today = date.today()
        week_ago = today - timedelta(days=6)

        # ---------- 当前用户的个人数据 ----------
        my_stats = (
            await self.db.execute(
                select(
                    func.count(Booking.id).label("total"),
                    _count_if(
                        Booking.status == BookingStatus.PENDING.value
                    ).label("pending"),
                ).where(Booking.user_id == user.id)
            )
        ).mappings().first()

        my_collections = (
            await self.db.execute(
                select(func.count())
                .select_from(EquipmentCollection)
                .where(EquipmentCollection.user_id == user.id)
            )
        ).scalar() or 0

        # ---------- 全局设备数据（所有角色都能看设备概况） ----------
        equipment_stats = (
            await self.db.execute(
                select(
                    func.count(Equipment.id).label("total"),
                    _count_if(
                        Equipment.status == EquipmentStatus.AVAILABLE.value
                    ).label("available"),
                    _count_if(
                        Equipment.status == EquipmentStatus.BUSY.value
                    ).label("busy"),
                )
            )
        ).mappings().first()

        # ---------- 近 7 天完成数 ----------
        # 注意：这里用 booking_date 而不是 updated_at 来判断"近7天完成"。
        # 因为 updated_at 会因为任何字段变更而刷新（比如管理员补了个备注），
        # 用它统计"完成时间"是不准确的。
        # 更严谨的做法是加一个 completed_at 字段专门记录完成时间，
        # 这里为了简洁用 booking_date（预约的使用日期）近似。
        completed_condition = [
            Booking.status == BookingStatus.COMPLETED.value,
            Booking.booking_date >= week_ago,
        ]

        if user.is_admin:
            weekly_completed = (
                await self.db.execute(
                    select(func.count()).select_from(Booking).where(*completed_condition)
                )
            ).scalar() or 0
        else:
            weekly_completed = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Booking)
                    .where(*completed_condition, Booking.user_id == user.id)
                )
            ).scalar() or 0

        data = {
            "my_pending_bookings": int(my_stats["pending"] or 0),
            "my_total_bookings": int(my_stats["total"] or 0),
            "my_collections": int(my_collections),
            "available_equipment": int(equipment_stats["available"] or 0),
            "total_equipment": int(equipment_stats["total"] or 0),
            "busy_equipment": int(equipment_stats["busy"] or 0),
            "all_pending_bookings": 0,
            "total_users": 0,
            "weekly_completed": int(weekly_completed),
            "statistics_time": datetime.now(),
        }

        # ---------- 管理员专属指标 ----------
        if user.is_admin:
            all_pending = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Booking)
                    .where(Booking.status == BookingStatus.PENDING.value)
                )
            ).scalar() or 0
            total_users = (
                await self.db.execute(
                    select(func.count()).select_from(User).where(User.status == "active")
                )
            ).scalar() or 0
            data["all_pending_bookings"] = int(all_pending)
            data["total_users"] = int(total_users)

        return data

    # ==========================================================================
    #  内部工具
    # ==========================================================================

    @staticmethod
    def _normalize_row(row: dict) -> dict:
        """
        把查询结果里的 Decimal 转成 float。

        为什么需要这一步？
        SQLAlchemy 的 Numeric 类型返回 Decimal（为了精度），
        但 JSON 序列化时 Decimal 会变成**字符串**（"123.45"），
        前端拿到字符串做数值运算/排序就会出错。
        统一转成 float 避免这类坑。
        """
        for key, value in row.items():
            if isinstance(value, Decimal):
                row[key] = float(value)
        return row
