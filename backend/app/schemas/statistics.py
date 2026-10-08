"""
统计相关的响应模型。

设计要点（面试可以讲）：

**"活跃用户"的定义问题**

原项目的统计接口是这样算"活跃用户"的：

    WHERE expires_at >= 30天前 AND is_revoked = 0

这其实是"Token 还没过期的用户数"，不是"活跃用户"。
因为本项目 Token 有效期是 7 天，那么"30 天内过期的 Token"
实际上就是"**所有**当前有效的 Token" —— 这个指标和"近30天活跃"毫无关系。

V2 的做法：加 `last_used_at` 字段，每次认证成功时更新，
统计时按 `last_used_at >= 30天前` 来算真正有访问行为的用户。

**这个差别在面试里值得讲**：它体现的是"指标定义要与业务含义一致"，
而不是"能算出来就用"。很多项目的数据看板都有这类"看起来有数据、
实际口径错了"的问题。
"""
from datetime import date, datetime
from typing import List, Optional

from pydantic import Field

from app.schemas.common import ORMModel


# ====================== 用户统计 ======================

class UserStatisticsResponse(ORMModel):
    """用户统计概览"""

    total_users: int = Field(0, description="用户总数（启用状态）")
    active_users_30d: int = Field(
        0, description="近 30 天有访问行为的用户数（基于 last_used_at 统计）"
    )
    online_sessions: int = Field(0, description="当前有效登录会话数（未过期且未撤销的 Token 数）")
    disabled_users: int = Field(0, description="被禁用的用户数")
    new_users_7d: int = Field(0, description="近 7 天新注册用户数")
    admin_count: int = Field(0, description="管理员数量")
    statistics_time: datetime = Field(..., description="统计时间")


# ====================== 设备统计 ======================

class EquipmentRankItem(ORMModel):
    """设备排行项"""

    equipment_id: int
    equipment_name: str
    category_name: Optional[str] = None
    lab_name: Optional[str] = None
    browse_count: int = Field(0, description="浏览量")
    booking_count: int = Field(0, description="累计完成预约次数")
    collect_count: int = Field(0, description="被收藏次数")
    active_booking_count: int = Field(0, description="当前有效预约数")


class EquipmentStatisticsResponse(ORMModel):
    """设备统计概览"""

    equipment_ranking: List[EquipmentRankItem] = Field(
        default_factory=list, description="设备排行（按浏览量倒序）"
    )
    total_browse_count: int = Field(0, description="全部设备浏览量之和")
    total_booking_count: int = Field(0, description="全部设备预约次数之和")
    total_collect_count: int = Field(0, description="收藏记录总数")
    total_equipment_count: int = Field(0, description="设备总数")
    statistics_time: datetime


class StatusDistributionItem(ORMModel):
    """设备状态分布项"""

    status: str = Field(..., description="状态值：available/busy/maintenance")
    status_name: str = Field(..., description="状态中文名")
    count: int = Field(0, description="该状态的设备数量")
    percentage: float = Field(0.0, description="占比（百分比，保留两位小数）")


class EquipmentStatusDistributionResponse(ORMModel):
    """设备状态分布（适配饼图）"""

    status_distribution: List[StatusDistributionItem] = Field(default_factory=list)
    total_equipment_count: int = Field(0)
    statistics_time: datetime


# ====================== 预约统计 ======================

class BookingStatusItem(ORMModel):
    """预约状态分布项"""

    status: str
    status_name: str
    count: int = 0
    percentage: float = 0.0


class BookingStatisticsResponse(ORMModel):
    """预约统计概览"""

    total_bookings: int = Field(0, description="预约总数")
    pending_count: int = Field(0, description="待审核数")
    approved_count: int = Field(0, description="已通过数")
    completed_count: int = Field(0, description="已完成数")
    rejected_count: int = Field(0, description="已拒绝数")
    cancelled_count: int = Field(0, description="已取消数")
    today_bookings: int = Field(0, description="今天的预约数")
    status_distribution: List[BookingStatusItem] = Field(default_factory=list)
    statistics_time: datetime


class DailyBookingItem(ORMModel):
    """每日预约数据项"""

    date: str = Field(..., description="日期（YYYY-MM-DD）")
    booking_count: int = Field(0, description="当天数量")
    label: str = Field("", description="展示用标签，如 10-08")


class WeeklyBookingResponse(ORMModel):
    """近 7 天预约趋势（适配折线图）"""

    daily_bookings: List[DailyBookingItem] = Field(default_factory=list)
    total_weekly_booking: int = Field(0, description="7 天合计")
    avg_daily_booking: float = Field(0.0, description="日均")
    max_daily_booking: int = Field(0, description="单日峰值")


# ====================== 仪表盘总览 ======================

class DashboardOverviewResponse(ORMModel):
    """
    首页仪表盘总览。

    把首页需要的所有数字汇总到一个接口里返回。
    为什么要这样做？因为原项目首页要发 **5 个** 请求
    （用户信息、待审核数、收藏数、周预约数、可用设备数），
    每个都是一次网络往返。合成一个接口能显著减少首屏时间 ——
    这是"聚合接口（BFF 模式）"的思路。
    """

    # 当前用户相关的
    my_pending_bookings: int = Field(0, description="我的待审核预约数")
    my_total_bookings: int = Field(0, description="我的预约总数")
    my_collections: int = Field(0, description="我的收藏数")

    # 全局的
    available_equipment: int = Field(0, description="可用设备数")
    total_equipment: int = Field(0, description="设备总数")
    busy_equipment: int = Field(0, description="使用中设备数")

    # 管理员才有的
    all_pending_bookings: int = Field(0, description="全平台待审核数（仅管理员有值）")
    total_users: int = Field(0, description="用户总数（仅管理员有值）")

    weekly_completed: int = Field(0, description="近 7 天完成数")
    statistics_time: datetime
