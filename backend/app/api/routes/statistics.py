"""
统计路由。

设计要点（面试可以讲）：
1. **权限分级**：统计模块分为两类
   - 个人相关（我的预约数、收藏数）→ 登录即可
   - 全局统计（用户总数、设备排行、状态分布）→ 管理员
   这个划分很重要：全局统计涉及"全平台有多少用户"这类信息，
   不应该让普通用户看到。

2. **聚合接口**：首页用 `/dashboard` 一次拿全部数字，
   避免前端发 5 个请求。
"""
import logging

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_admin
from app.core.response import Response, success
from app.schemas.statistics import (
    BookingStatisticsResponse,
    DashboardOverviewResponse,
    EquipmentStatisticsResponse,
    EquipmentStatusDistributionResponse,
    UserStatisticsResponse,
    WeeklyBookingResponse,
)
from app.services.statistics_service import StatisticsService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/statistics", tags=["数据统计"])


# ====================== 仪表盘（聚合接口）======================

@router.get(
    "/dashboard",
    response_model=Response[DashboardOverviewResponse],
    summary="首页仪表盘总览",
    description=(
        "一次返回首页需要的所有统计数字，避免前端发多个请求。\n\n"
        "普通用户只会拿到自己的数据和设备概况；"
        "管理员额外拿到全平台待审核数和用户总数。"
    ),
)
async def get_dashboard(
    current_user: CurrentUser,
    db: DbSession,
) -> Response[DashboardOverviewResponse]:
    service = StatisticsService(db)
    data = await service.get_dashboard_overview(current_user)
    return success(data=DashboardOverviewResponse(**data), message="获取仪表盘数据成功")


# ====================== 个人统计（登录即可）======================

@router.get(
    "/my/summary",
    response_model=Response[dict],
    summary="我的数据概览",
    description="我的预约数、收藏数、待审核数。普通用户可访问。",
)
async def get_my_summary(
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    """
    个人数据概览。

    注意：这个接口只返回当前用户自己的数据，
    不涉及任何全局信息，所以不需要管理员权限。
    """
    service = StatisticsService(db)
    data = await service.get_dashboard_overview(current_user)
    return success(
        data={
            "my_pending_bookings": data["my_pending_bookings"],
            "my_total_bookings": data["my_total_bookings"],
            "my_collections": data["my_collections"],
            "weekly_completed": data["weekly_completed"],
        },
        message="获取个人统计成功",
    )


# ====================== 全局统计（管理员）======================

@router.get(
    "/users",
    response_model=Response[UserStatisticsResponse],
    summary="用户统计（管理员）",
    description=(
        "**关于「活跃用户」的口径说明**：\n\n"
        "这里统计的是「近 30 天内有实际访问行为的用户数」，"
        "依据是每次认证成功时更新的 `last_used_at` 字段。\n\n"
        "有些实现会用「Token 未过期」来代表活跃，那是不准确的 ——"
        "Token 有效期与用户是否真的在用系统是两件事。"
    ),
    dependencies=[Depends(require_admin)],
)
async def get_user_statistics(db: DbSession) -> Response[UserStatisticsResponse]:
    service = StatisticsService(db)
    data = await service.get_user_statistics()
    return success(data=UserStatisticsResponse(**data), message="获取用户统计成功")


@router.get(
    "/equipment",
    response_model=Response[EquipmentStatisticsResponse],
    summary="设备统计与排行（管理员）",
    description="支持按浏览量、预约次数、收藏数排序取 Top N。",
    dependencies=[Depends(require_admin)],
)
async def get_equipment_statistics(
    db: DbSession,
    limit: int = Query(10, ge=1, le=100, description="返回条数"),
    order_by: str = Query(
        "browse_count",
        pattern="^(browse_count|booking_count|collect_count)$",
        description="排序依据",
    ),
) -> Response[EquipmentStatisticsResponse]:
    service = StatisticsService(db)
    data = await service.get_equipment_statistics(limit=limit, order_by=order_by)
    return success(data=EquipmentStatisticsResponse(**data), message="获取设备统计成功")


@router.get(
    "/equipment/status",
    response_model=Response[EquipmentStatusDistributionResponse],
    summary="设备状态分布（管理员）",
    description=(
        "适配饼图。所有预定义状态都会返回，即使某个状态数量为 0 "
        "（避免前端饼图缺块）。"
    ),
    dependencies=[Depends(require_admin)],
)
async def get_equipment_status_distribution(
    db: DbSession,
) -> Response[EquipmentStatusDistributionResponse]:
    service = StatisticsService(db)
    data = await service.get_equipment_status_distribution()
    return success(
        data=EquipmentStatusDistributionResponse(**data), message="获取设备状态分布成功"
    )


@router.get(
    "/bookings",
    response_model=Response[BookingStatisticsResponse],
    summary="预约统计（管理员）",
    dependencies=[Depends(require_admin)],
)
async def get_booking_statistics(db: DbSession) -> Response[BookingStatisticsResponse]:
    service = StatisticsService(db)
    data = await service.get_booking_statistics()
    return success(data=BookingStatisticsResponse(**data), message="获取预约统计成功")


@router.get(
    "/bookings/trend",
    response_model=Response[WeeklyBookingResponse],
    summary="预约趋势（管理员）",
    description=(
        "适配折线图。**统计口径**：按预约的**创建日期**分组，"
        "反映「每天有多少预约被提交」，而不是「每天使用了多少次设备」。\n\n"
        "缺失的日期会自动补 0，保证折线连续。"
    ),
    dependencies=[Depends(require_admin)],
)
async def get_booking_trend(
    db: DbSession,
    days: int = Query(7, ge=3, le=30, description="统计天数"),
) -> Response[WeeklyBookingResponse]:
    service = StatisticsService(db)
    data = await service.get_weekly_booking_trend(days=days)
    return success(data=WeeklyBookingResponse(**data), message="获取预约趋势成功")
