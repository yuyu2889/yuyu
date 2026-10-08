"""
预约服务：创建、查询、审核、取消。

**这是整个项目最重要的模块，包含并发控制、状态机、冲突检测三大核心逻辑。**

面试时可以这样讲这一块的难点：
  预约系统看起来就是"增删改查"，但真正的难点有三个：
  1. 时间区间冲突怎么判定（算法问题）
  2. 并发提交怎么防（并发问题）
  3. 状态流转怎么保证不出错（状态机问题）
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    BOOKING_ACTIVE_STATUSES,
    BookingStatus,
    EquipmentStatus,
    can_transition,
)
from app.core.exceptions import (
    BusinessError,
    ConflictError,
    NotFoundError,
    PermissionError_,
)
from app.core.lock import LockKey, distributed_lock
from app.core.response import ErrorCode, PageData
from app.core.security import utc_now
from app.models.booking import Booking
from app.repositories.booking import BookingRepository
from app.repositories.equipment import EquipmentRepository
from app.repositories.user import UserRepository
from app.schemas.booking import (
    BookingAuditRequest,
    BookingCreateRequest,
    BookingResponse,
)
from app.services.notification import NotificationService
from app.utils.url import to_static_url

logger = logging.getLogger(__name__)

# 单用户最多同时持有的有效预约数（防止恶意占满所有设备）
MAX_ACTIVE_BOOKINGS_PER_USER = 10


class BookingService:
    """预约业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = BookingRepository(db)
        self.equipment_repo = EquipmentRepository(db)
        self.user_repo = UserRepository(db)
        self.notification = NotificationService(db)

    # ==========================================================================
    #  创建预约（并发控制的核心）
    # ==========================================================================

    async def create_booking(self, user_id: int, data: BookingCreateRequest) -> BookingResponse:
        """
        创建预约。

        ┌─────────────────────────────────────────────────────────────────────┐
        │  并发问题说明（面试重点）                                            │
        │                                                                     │
        │  朴素实现是这样的：                                                  │
        │      conflicts = await check_conflict(...)   # 查有没有冲突          │
        │      if conflicts: raise Error               # 有就报错              │
        │      await insert(...)                       # 没有就插入            │
        │                                                                     │
        │  两个并发请求会这样交错执行：                                        │
        │      请求A: 查询冲突 → 无                                            │
        │      请求B: 查询冲突 → 无                                            │
        │      请求A: 插入成功                                                 │
        │      请求B: 插入成功   ← 冲突了！但两次检查都通过了                   │
        │                                                                     │
        │  这就是 TOCTOU（Time-Of-Check to Time-Of-Use）竞态。                 │
        └─────────────────────────────────────────────────────────────────────┘

        **本项目的三层防护**：

        第 1 层：Redis 分布式锁（按用户维度）
                让"检查 + 插入"变成原子操作。同一用户的并发请求会串行执行，
                第二个请求进入临界区时会看到第一个请求已插入的数据，检测出冲突。

        第 2 层：事务内冲突检测（区间重叠算法）
                保证业务逻辑正确性。即使锁失效（Redis 挂了），
                只要检测逻辑对，单线程下结果就是对的。

        第 3 层：数据库唯一约束 + 事务
                兜底。极端情况下（比如多实例部署且 Redis 分区），
                数据库层不会产生重复数据，应用层捕获 IntegrityError 返回友好提示。

        **为什么锁的粒度选「用户」而不是「设备」？**
        见 app/core/lock.py 里 LockKey 的说明。简单说：
        锁用户维度能防住最核心的场景（同一用户狂点提交），
        同时并发能力最好（不同用户互不阻塞）。
        """
        # ---------- 锁外的前置校验（不需要锁的检查先做，减少持锁时间）----------
        # 原则：能在锁外做的检查就不要放到锁内，尽量缩短临界区。
        equipment = await self.equipment_repo.get(data.equipment_id)
        if equipment is None:
            raise NotFoundError(ErrorCode.EQUIPMENT_NOT_FOUND)

        # ⚠️ 这里刻意**不检查设备当前是否为 maintenance（维护中）**。
        #
        # 为什么？（这是一个容易想错的地方）
        # 用户预约的是**未来**的某个时段（最早今天、最晚 30 天后）。
        # "设备此刻在维护"和"设备在预约那天能不能用"是两件不同的事 ——
        # 现在维护不代表三天后还在维护。
        # 如果按当前状态直接拒绝，会出现"设备今天送修，未来一个月的预约全约不了"
        # 这种不合理的结果。
        #
        # 正确的做法（本项目的选择）：
        #   1. 冲突检测只关心"有没有别的预约占用了这个时段"（下面会查）
        #   2. 设备是否可用的最终判定权在**管理员审核**环节 ——
        #      管理员知道设备的实际维护计划，由人来决定是否批准
        #   3. 如果设备在预约期间真的进入了维护，管理员可以手动
        #      把该设备的预约取消（cancel 接口支持管理员操作）
        #
        # 换一种更严格的设计也可以：给设备加"维护计划时间段"，
        # 用区间重叠算法判断预约是否落在维护期内。
        # 但那需要额外的维护计划表，对毕设项目来说过重了。
        # 这里在代码里记录这个取舍，是为了说明"知道边界在哪"。

        # 用户已有太多有效预约 → 拒绝（防恶意占用）
        user_active = await self._count_user_active_bookings(user_id)
        if user_active >= MAX_ACTIVE_BOOKINGS_PER_USER:
            raise BusinessError(
                ErrorCode.CONFLICT,
                f"您当前有 {user_active} 条未完成的预约，"
                f"已达上限（{MAX_ACTIVE_BOOKINGS_PER_USER} 条），请先完成或取消部分预约",
            )

        # ---------- 进入临界区（加锁）----------
        async with distributed_lock(
            LockKey.booking_by_user(user_id),
            timeout=15,
            wait_timeout=4.0,
            error_message="您有另一个预约请求正在处理中，请稍后重试",
        ):
            # ========== 第 2.5 层：数据库行锁（关键！）==========
            # 这一步是"读视图陷阱"的解法，详见 EquipmentRepository.lock_for_booking
            # 的注释。简单说：
            #   MySQL REPEATABLE READ 下，事务读视图在认证查用户时就固定了，
            #   导致拿到 Redis 锁后查冲突仍然看到旧快照 → 检测不出冲突。
            #   SELECT ... FOR UPDATE 是"当前读"，能读到最新已提交数据，
            #   且会阻塞并发事务，从而真正实现串行化。
            #
            # 顺序说明：先拿 Redis 锁（快速失败、减少数据库锁等待），
            # 再拿数据库行锁（保证正确性）。反过来也能work，但会让
            # 大量并发请求都堆积在数据库锁上，浪费连接。
            locked_equipment = await self.equipment_repo.lock_for_booking(data.equipment_id)
            if locked_equipment is None:
                raise NotFoundError(ErrorCode.EQUIPMENT_NOT_FOUND)

            # ---------- 第 2 层：事务内冲突检测 ----------
            # 1) 用户维度：同一用户同一时段不能约两台设备
            #    产品逻辑：一个人不可能同时用两台设备
            #
            #    ⚠️ 注意这里必须用 fresh_session 语义 —— 因为上面的
            #    FOR UPDATE 已经让本事务进入"当前读"模式，后续 SELECT
            #    会读到最新已提交数据。
            user_conflicts = await self.repo.find_user_conflicts(
                user_id=user_id,
                booking_date=data.booking_date,
                start_time=data.start_time,
                end_time=data.end_time,
            )
            if user_conflicts:
                conflict = user_conflicts[0]
                equipment_name = (
                    conflict.equipment.name if conflict.equipment else f"设备{conflict.equipment_id}"
                )
                raise ConflictError(
                    ErrorCode.BOOKING_USER_CONFLICT,
                    f"您在该时段已有预约「{equipment_name}」"
                    f"（{conflict.start_time.strftime('%H:%M')}-"
                    f"{conflict.end_time.strftime('%H:%M')}），"
                    "同一时段只能预约一台设备",
                )

            # 2) 设备维度：该设备同一时段已被别人预约
            equipment_conflicts = await self.repo.find_equipment_conflicts(
                equipment_id=data.equipment_id,
                booking_date=data.booking_date,
                start_time=data.start_time,
                end_time=data.end_time,
            )
            if equipment_conflicts:
                slots = [
                    f"{c.start_time.strftime('%H:%M')}-{c.end_time.strftime('%H:%M')}"
                    for c in equipment_conflicts
                ]
                raise ConflictError(
                    ErrorCode.BOOKING_EQUIPMENT_CONFLICT,
                    f"设备「{equipment.name}」在 {data.booking_date} 的 "
                    f"{'、'.join(slots)} 已被预约，请选择其他时段",
                )

            # ---------- 第 3 层：插入（唯一约束兜底）----------
            booking = Booking(
                user_id=user_id,
                equipment_id=data.equipment_id,
                booking_date=data.booking_date,
                start_time=data.start_time,
                end_time=data.end_time,
                purpose=data.purpose,
                notes=data.notes,
                status=BookingStatus.PENDING.value,
                is_counted=0,
            )
            self.db.add(booking)
            await self.db.flush()

            # 重新加载以带上关联对象（响应需要设备名等信息）
            booking = await self.repo.get_with_relations(booking.id)

        # 提交事务（在锁外提交也可以，因为数据已经写入且锁已经"用完了"）
        await self.db.commit()

        logger.info(
            "创建预约成功 | booking_id=%s | user_id=%s | equipment=%s | %s %s-%s",
            booking.id, user_id, equipment.name, data.booking_date,
            data.start_time, data.end_time,
        )

        # 发送通知邮件（异步，不阻塞响应）
        await self.notification.notify_booking_created(booking)

        return self._to_response(booking)

    # ==========================================================================
    #  冲突预检（只读，不加锁）
    # ==========================================================================

    async def check_conflict(
        self,
        user_id: int,
        equipment_id: int,
        booking_date: date,
        start_time: time,
        end_time: time,
    ) -> dict:
        """
        冲突预检。

        前端在用户填完时间、还没提交时调用，提前给出反馈。
        体验价值：避免用户填了一堆信息点提交才被拒绝。

        注意：预检结果**仅供参考**，不能作为"保证能预约成功"的依据
        （预检和真正提交之间可能被别人抢先）。
        真正的校验必须在 create_booking 里再做一次 ——
        这是一个重要的设计意识：**预检是体验优化，不是正确性保障**。
        """
        result = {
            "available": True,
            "reason": None,
            "conflicts": [],
            "booked_slots": [],
        }

        # 设备是否存在
        # 注意：和 create_booking 一样，这里不按"当前是否维护"来拒绝，
        # 因为预约的是未来时段。只做存在性检查。
        equipment = await self.equipment_repo.get(equipment_id)
        if equipment is None:
            result.update(available=False, reason="设备不存在")
            return result

        # 设备当前在维护中 → 给出提示但仍允许预检通过
        # （把最终判断权交给管理员审核环节，理由见 create_booking 里的说明）
        if equipment.status == EquipmentStatus.MAINTENANCE.value:
            result["reason"] = (
                f"注意：设备「{equipment.name}」当前处于维护状态，"
                "预约需要管理员确认设备可用后才能通过审核"
            )

        # ⚠️ 注意这里用的是 **_readonly 版本**（不加 FOR UPDATE 行锁）。
        # 预检是只读操作，不应该加锁 —— 否则用户每点一次"检查可用性"
        # 都会阻塞其他用户的正常预约，把系统并发能力拖垮。
        # 预检结果本来就允许"稍微不准"（提交时会用加锁版本严格校验）。
        user_conflicts = await self.repo.find_user_conflicts_readonly(
            user_id, booking_date, start_time, end_time
        )
        if user_conflicts:
            c = user_conflicts[0]
            result.update(
                available=False,
                reason=f"您在该时段已有预约"
                       f"（{c.start_time.strftime('%H:%M')}-{c.end_time.strftime('%H:%M')}）",
            )

        # 设备冲突
        equipment_conflicts = await self.repo.find_equipment_conflicts_readonly(
            equipment_id, booking_date, start_time, end_time
        )
        if equipment_conflicts:
            result.update(
                available=False,
                reason=f"该设备在此时间段已被预约（共 {len(equipment_conflicts)} 条冲突）",
            )

        # 返回冲突详情（脱敏：不含预约人信息）
        result["conflicts"] = [
            {
                "start_time": str(c.start_time),
                "end_time": str(c.end_time),
                "status": c.status,
            }
            for c in list(equipment_conflicts)
        ]

        # 当天已占用时段（供前端画时间轴）
        slots = await self.repo.get_booked_slots(equipment_id, booking_date)
        result["booked_slots"] = [
            {"start_time": str(s), "end_time": str(e), "status": st} for s, e, st in slots
        ]

        return result

    # ==========================================================================
    #  查询
    # ==========================================================================

    async def list_my_bookings(
        self,
        user_id: int,
        page_params,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> PageData[BookingResponse]:
        """我的预约列表"""
        bookings, total = await self.repo.list_by_user(
            user_id=user_id,
            offset=page_params.offset,
            limit=page_params.page_size,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        items = [self._to_response(b) for b in bookings]
        return PageData.build(items, total, page_params.page, page_params.page_size)

    async def list_all_bookings(
        self,
        page_params,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        equipment_id: Optional[int] = None,
        keyword: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> PageData[BookingResponse]:
        """管理员查询所有预约"""
        bookings, total = await self.repo.list_all(
            offset=page_params.offset,
            limit=page_params.page_size,
            status=status,
            user_id=user_id,
            equipment_id=equipment_id,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
        )
        items = [self._to_response(b) for b in bookings]
        return PageData.build(items, total, page_params.page, page_params.page_size)

    async def get_booking(self, booking_id: int, current_user_id: int, is_admin: bool) -> BookingResponse:
        """
        查询预约详情。

        权限设计：**资源级权限**
        不是简单的"登录就能看"，而是"只能看自己的，管理员能看全部"。
        这类"资源归属校验"是接口安全的重要一环 ——
        如果只校验"已登录"，任何用户改一下 URL 里的 ID 就能看到别人的数据
        （这叫 IDOR，越权访问漏洞）。
        """
        booking = await self.repo.get_with_relations(booking_id)
        if booking is None:
            raise NotFoundError(ErrorCode.BOOKING_NOT_FOUND)

        if booking.user_id != current_user_id and not is_admin:
            raise PermissionError_("只能查看自己的预约")

        return self._to_response(booking)

    # ==========================================================================
    #  审核
    # ==========================================================================

    async def audit_booking(
        self,
        booking_id: int,
        admin_id: int,
        data: BookingAuditRequest,
    ) -> BookingResponse:
        """
        管理员审核预约。

        审核通过时需要再做一次设备冲突检测 ——
        为什么？因为从用户提交到管理员审核之间可能过了很久，
        期间可能有别的预约被批准占用了同一时段。

        这是"延时校验"的典型场景：**校验结论会随时间失效，
        所以在真正执行关键操作前必须重新校验一次**。
        很多人只在创建时校验，审核时忘了，就会产生重叠的已通过预约。
        """
        async with distributed_lock(
            LockKey.booking_audit(booking_id),
            timeout=15,
            wait_timeout=4.0,
            error_message="该预约正在被审核中，请稍后重试",
        ):
            booking = await self.repo.get_with_relations(booking_id)
            if booking is None:
                raise NotFoundError(ErrorCode.BOOKING_NOT_FOUND)

            # 状态机校验：只有 pending 能审核
            if not can_transition(
                BookingStatus(booking.status), data.result
            ):
                if booking.status != BookingStatus.PENDING.value:
                    raise BusinessError(
                        ErrorCode.BOOKING_ALREADY_AUDITED,
                        f"该预约当前状态为「{booking.status}」，不能重复审核",
                    )
                raise BusinessError(
                    ErrorCode.BOOKING_STATUS_INVALID,
                    f"不允许从 {booking.status} 变更为 {data.result.value}",
                )

            # 审核通过前重新检测冲突
            if data.result == BookingStatus.APPROVED:
                conflicts = await self.repo.find_equipment_conflicts(
                    equipment_id=booking.equipment_id,
                    booking_date=booking.booking_date,
                    start_time=booking.start_time,
                    end_time=booking.end_time,
                    exclude_booking_id=booking_id,
                )
                if conflicts:
                    slots = [
                        f"{c.start_time.strftime('%H:%M')}-{c.end_time.strftime('%H:%M')}"
                        for c in conflicts
                    ]
                    raise ConflictError(
                        ErrorCode.BOOKING_EQUIPMENT_CONFLICT,
                        f"无法通过：该设备在此时段已有其他通过的预约"
                        f"（{booking.booking_date} {'、'.join(slots)}）",
                    )

            old_status = booking.status
            await self.repo.update_status(
                booking_id=booking_id,
                new_status=data.result,
                auditor_id=admin_id,
                audit_note=data.note,
            )
            await self.db.commit()

            booking = await self.repo.get_with_relations(booking_id)

        logger.info(
            "预约审核完成 | booking_id=%s | admin_id=%s | %s -> %s",
            booking_id, admin_id, old_status, data.result.value,
        )

        # 通知用户审核结果
        await self.notification.notify_booking_audited(booking, data.result.value, data.note)

        return self._to_response(booking)

    # ==========================================================================
    #  取消
    # ==========================================================================

    async def cancel_booking(
        self,
        booking_id: int,
        user_id: int,
        is_admin: bool = False,
        reason: Optional[str] = None,
    ) -> BookingResponse:
        """
        取消预约。

        两种角色都可以取消：
        - 用户：只能取消自己的
        - 管理员：可以取消任何人的（比如设备临时故障需要取消所有预约）

        取消已通过的预约时，如果该时段正在进行中，需要把设备状态恢复为可用。
        原项目有这段逻辑但写得不完整（只在"正在进行中"才恢复）。
        V2 的做法更严谨：交给定时任务的兜底扫描来处理，
        service 层只做必要的即时恢复。
        """
        booking = await self.repo.get_with_relations(booking_id)
        if booking is None:
            raise NotFoundError(ErrorCode.BOOKING_NOT_FOUND)

        # 资源级权限校验
        if booking.user_id != user_id and not is_admin:
            raise PermissionError_(ErrorCode.BOOKING_NOT_OWNER, "只能取消自己的预约")

        # 状态机校验：只有 pending / approved 能取消
        if not can_transition(BookingStatus(booking.status), BookingStatus.CANCELLED):
            raise BusinessError(
                ErrorCode.BOOKING_STATUS_INVALID,
                f"当前状态为「{booking.status}」，不能取消"
                "（只有待审核和已通过的预约可以取消）",
            )

        old_status = booking.status
        await self.repo.update_status(
            booking_id=booking_id,
            new_status=BookingStatus.CANCELLED,
            audit_note=reason,
        )

        # 如果取消的是"正在进行中"的已通过预约，立刻把设备恢复为可用
        # （不用等定时任务，用户体验更好）
        restored = False
        if old_status == BookingStatus.APPROVED.value:
            now = datetime.now()
            if booking.start_datetime <= now <= booking.end_datetime:
                # 检查是否还有别的进行中的预约占用这台设备
                others = await self.repo.find_equipment_conflicts(
                    equipment_id=booking.equipment_id,
                    booking_date=booking.booking_date,
                    start_time=booking.start_time,
                    end_time=booking.end_time,
                    exclude_booking_id=booking_id,
                )
                if not others:
                    equipment = await self.equipment_repo.get(booking.equipment_id)
                    if equipment and equipment.status == EquipmentStatus.BUSY.value:
                        equipment.status = EquipmentStatus.AVAILABLE.value
                        restored = True

        await self.db.commit()

        booking = await self.repo.get_with_relations(booking_id)
        logger.info(
            "取消预约 | booking_id=%s | 操作者=%s(%s) | %s -> cancelled | 设备状态恢复=%s",
            booking_id, user_id, "管理员" if is_admin else "用户",
            old_status, restored,
        )
        return self._to_response(booking)

    # ==========================================================================
    #  内部方法
    # ==========================================================================

    async def _count_user_active_bookings(self, user_id: int) -> int:
        """统计用户的未完成预约数"""
        from sqlalchemy import func, select

        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.user_id == user_id,
                Booking.status.in_([s.value for s in BOOKING_ACTIVE_STATUSES]),
            )
        )
        return (await self.db.execute(stmt)).scalar() or 0

    async def count_my_bookings_by_status(self, user_id: int, status: str) -> int:
        """
        统计某用户指定状态的预约数。

        用于首页"待审核预约"卡片，避免为了拿一个数字而拉一整页数据。
        """
        from sqlalchemy import func, select

        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(Booking.user_id == user_id, Booking.status == status)
        )
        return (await self.db.execute(stmt)).scalar() or 0

    @staticmethod
    def _to_response(booking: Booking) -> BookingResponse:
        """
        把 ORM 预约对象转成响应模型。

        这里做了几件事：
        1. 扁平化 user_name / equipment_name（方便列表直接显示）
        2. 转换图片路径
        3. 计算 can_cancel / can_audit（前端不用自己写状态判断）
        """
        equipment = booking.equipment
        user = booking.user

        return BookingResponse(
            id=booking.id,
            status=booking.status,
            booking_date=booking.booking_date,
            start_time=booking.start_time,
            end_time=booking.end_time,
            purpose=booking.purpose,
            notes=booking.notes,
            audit_note=booking.audit_note,
            audited_at=booking.audited_at,
            user_id=booking.user_id,
            user_name=(user.real_name or user.username) if user else None,
            equipment_id=booking.equipment_id,
            equipment_name=equipment.name if equipment else None,
            equipment_image=equipment.image if equipment else None,
            equipment={
                "id": equipment.id,
                "name": equipment.name,
                "model": equipment.model,
                "image": equipment.image,
            } if equipment else None,
            user={
                "id": user.id,
                "username": user.username,
                "real_name": user.real_name,
            } if user else None,
            created_at=booking.created_at,
            updated_at=booking.updated_at,
            # 前端可以直接用这两个字段决定按钮是否可点，不用自己判断状态
            can_cancel=booking.can_be_cancelled,
            can_audit=booking.can_be_audited,
        )
