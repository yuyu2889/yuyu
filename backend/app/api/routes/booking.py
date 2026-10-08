"""
预约路由。

设计要点（面试可以讲）：
1. **BackgroundTasks 的正确用法**
   邮件通知用 BackgroundTasks 而不是 asyncio.create_task：
   - BackgroundTasks 由 Starlette 管理，保证在响应返回后执行
   - 更重要的一点：**邮件数据在请求内就组装好了**，后台任务不碰数据库
     （原项目复用请求会话导致邮件时好时坏，这是 V2 的修复点）

2. **资源级权限**
   查询预约详情时不是"登录就能看"，而是校验"是不是自己的预约"
   或"是不是管理员"。这防的是 IDOR（越权访问）漏洞 ——
   只改 URL 里的 ID 就能看到别人的数据。

3. **冲突预检接口**
   `POST /bookings/check-conflict` 只读、不加锁，
   前端可以在用户提交前先问一次，提前给出反馈。
   但要明确：预检结果只是体验优化，真正的校验在创建时还会再做一遍。
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    PageParamsDep,
    require_admin,
)
from app.core.enums import BookingStatus
from app.core.rate_limit import booking_create_limiter
from app.core.response import PageData, Response, success
from app.schemas.booking import (
    BookingAuditRequest,
    BookingCancelRequest,
    BookingConflictCheckRequest,
    BookingConflictCheckResponse,
    BookingCreateRequest,
    BookingResponse,
)
from app.services.booking_service import BookingService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bookings", tags=["预约"])


# ====================== 创建与预检 ======================

@router.post(
    "/check-conflict",
    response_model=Response[BookingConflictCheckResponse],
    summary="冲突预检（不创建预约）",
    description=(
        "在用户提交预约前检查该时段是否可用，可用于前端实时提示。\n\n"
        "**注意**：预检结果仅供参考，不代表一定预约成功"
        "（预检到真正提交之间可能被别人抢先占用）。"
    ),
)
async def check_conflict(
    data: BookingConflictCheckRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[BookingConflictCheckResponse]:
    service = BookingService(db)
    result = await service.check_conflict(
        user_id=current_user.id,
        equipment_id=data.equipment_id,
        booking_date=data.booking_date,
        start_time=data.start_time,
        end_time=data.end_time,
    )
    return success(
        data=BookingConflictCheckResponse(**result),
        message="该时段可用" if result["available"] else result["reason"],
    )


@router.post(
    "",
    response_model=Response[BookingResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建预约",
    description=(
        "**并发安全**：使用 Redis 分布式锁按用户维度串行化，"
        "配合事务内冲突检测和数据库唯一约束，三层防护避免重复预约。\n\n"
        "**校验规则**：\n"
        "- 时间必须对齐到整点或半点\n"
        "- 单次 30 分钟 ~ 8 小时\n"
        "- 可预约时间窗口 08:00 ~ 22:00\n"
        "- 只能预约今天起 30 天内\n"
        "- 同一时段只能预约一台设备（用户维度冲突）\n"
        "- 同一设备同一时段不能被两人预约（设备维度冲突）"
    ),
)
async def create_booking(
    data: BookingCreateRequest,
    request: Request,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[BookingResponse]:
    """
    创建预约。

    限流：按用户维度限制创建频率，防止脚本刷预约。
    注意这里用的是 user_id 而不是 IP ——
    因为一个用户可能换网络，而 IP 也可能被多人共用（NAT）。
    按用户维度限流更精准。
    """
    booking_create_limiter.check(f"booking:create:user:{current_user.id}")

    service = BookingService(db)
    booking = await service.create_booking(current_user.id, data)

    return success(
        data=booking,
        message="预约申请已提交，请等待管理员审核（审核结果将发送到您的邮箱）",
    )


# ====================== 统计辅助 ======================

@router.get(
    "/stats/pending-count",
    response_model=Response[dict],
    summary="待审核数量",
    description="首页卡片用。管理员返回全平台待审核数，普通用户返回自己的待审核数。",
)
async def get_pending_count(
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    """
    待审核数量。

    路由顺序说明：这个接口的路径是 /stats/pending-count（两段），
    而 /{booking_id} 只匹配一段，所以实际上不会被抢占匹配。
    但为了保险和可读性，所有固定路径的接口都写在 /{booking_id} 之前 ——
    这是一个值得养成的习惯：**固定路径永远放在动态路径前面**。
    如果哪天有人加了个 /stats 这样的接口，顺序错了就会被 {booking_id} 拦截，
    报出"stats 不是合法整数"这种让人摸不着头脑的错误。
    """
    service = BookingService(db)

    if current_user.is_admin:
        count = await service.repo.list_pending_count()
        scope = "all"
    else:
        count = await service.count_my_bookings_by_status(
            current_user.id, BookingStatus.PENDING.value
        )
        scope = "own"

    return success(data={"count": count, "scope": scope}, message="查询成功")


# ====================== 查询 ======================

@router.get(
    "/my",
    response_model=Response[PageData[BookingResponse]],
    summary="我的预约列表",
)
async def list_my_bookings(
    current_user: CurrentUser,
    db: DbSession,
    page_params: PageParamsDep,
    status_filter: str = Query(
        None, alias="status",
        description="状态筛选：pending/approved/rejected/cancelled/completed",
    ),
    date_from: date = Query(None, description="起始日期（含）"),
    date_to: date = Query(None, description="结束日期（含）"),
) -> Response[PageData[BookingResponse]]:
    service = BookingService(db)
    page = await service.list_my_bookings(
        user_id=current_user.id,
        page_params=page_params,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
    )
    return success(data=page, message="查询成功")


@router.get(
    "",
    response_model=Response[PageData[BookingResponse]],
    summary="所有预约列表（管理员）",
    description="支持按状态、用户、设备、关键字、日期范围筛选。",
    dependencies=[Depends(require_admin)],
)
async def list_all_bookings(
    db: DbSession,
    page_params: PageParamsDep,
    status_filter: str = Query(None, alias="status", description="状态筛选"),
    user_id: int = Query(None, description="按预约人筛选"),
    equipment_id: int = Query(None, description="按设备筛选"),
    keyword: str = Query(None, description="关键字：匹配预约人姓名/设备名/使用目的"),
    date_from: date = Query(None, description="起始日期"),
    date_to: date = Query(None, description="结束日期"),
) -> Response[PageData[BookingResponse]]:
    """
    管理员查询所有预约。

    安全说明：这个接口**必须**要求管理员权限。
    原项目的同名接口没有任何权限校验，注释里甚至写着"无需登录，
    所有用户均可访问"，导致任何人都能拉到全平台的预约数据
    （包含用户真实姓名、设备、使用目的）。
    """
    service = BookingService(db)
    page = await service.list_all_bookings(
        page_params=page_params,
        status=status_filter,
        user_id=user_id,
        equipment_id=equipment_id,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    return success(data=page, message="查询成功")


@router.get(
    "/{booking_id}",
    response_model=Response[BookingResponse],
    summary="预约详情",
    description="只能查看自己的预约；管理员可以查看全部。",
)
async def get_booking(
    booking_id: int = Path(..., gt=0, description="预约ID"),
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> Response[BookingResponse]:
    service = BookingService(db)
    booking = await service.get_booking(
        booking_id=booking_id,
        current_user_id=current_user.id,
        is_admin=current_user.is_admin,
    )
    return success(data=booking, message="查询成功")


# ====================== 审核 ======================

@router.post(
    "/{booking_id}/audit",
    response_model=Response[BookingResponse],
    summary="审核预约（管理员）",
    description=(
        "**审核通过前会重新做一次冲突检测** —— 因为从用户提交到管理员审核"
        "之间可能过了很久，期间可能有其他预约被批准占用了同一时段。\n\n"
        "拒绝时必须填写原因。"
    ),
    dependencies=[Depends(require_admin)],
)
async def audit_booking(
    data: BookingAuditRequest,
    booking_id: int = Path(..., gt=0),
    admin: AdminUser = None,
    db: DbSession = None,
) -> Response[BookingResponse]:
    service = BookingService(db)
    booking = await service.audit_booking(booking_id, admin.id, data)

    action = "通过" if data.result == BookingStatus.APPROVED else "拒绝"
    return success(
        data=booking,
        message=f"预约审核{action}成功（审核结果已发送到预约人邮箱）",
    )


# ====================== 取消 ======================

@router.put(
    "/{booking_id}/cancel",
    response_model=Response[BookingResponse],
    summary="取消预约",
    description=(
        "用户只能取消自己的预约，管理员可以取消任何预约。\n\n"
        "只有「待审核」和「已通过」状态的预约可以取消。\n\n"
        "如果取消的是正在进行中的预约，设备状态会立刻恢复为可用。"
    ),
)
async def cancel_booking(
    booking_id: int = Path(..., gt=0, description="预约ID"),
    data: BookingCancelRequest = None,
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> Response[BookingResponse]:
    service = BookingService(db)
    booking = await service.cancel_booking(
        booking_id=booking_id,
        user_id=current_user.id,
        is_admin=current_user.is_admin,
        reason=data.reason if data else None,
    )
    return success(data=booking, message="预约已取消")
