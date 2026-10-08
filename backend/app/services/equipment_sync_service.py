"""
设备状态同步定时任务。

**这段逻辑面试价值很高，因为它体现了"最终一致性"和"幂等"两个重要概念。**

业务流程：
  预约被审核通过（approved）
      ↓ 到了开始时间
  设备状态 → busy（使用中）
      ↓ 到了结束时间
  设备状态 → available（可用）
  预约状态 → completed（已完成）
  设备 booking_count + 1（且只加一次）

为什么需要定时任务而不是"到点自动触发"？
因为"到点触发"需要精确的一次性调度（比如延迟队列），
实现复杂且容易丢失（服务重启就丢了）。
用"周期性扫描 + 状态比对"是更简单可靠的方案 ——
即使服务停了一天，重启后扫描一次就能把所有状态修正过来。
这种"不追求实时、只保证最终正确"的思路叫**最终一致性**。

**两个关键技术点**：

1. **幂等（idempotent）**
   定时任务每 60 秒跑一次，同一条数据会被反复扫描到。
   如果用"预约结束就 booking_count + 1"，扫描两次就加两次。
   解法：用 is_counted 标记 —— 加之前先检查，加完立刻标记。
   这样无论扫描多少次，结果都一样，这叫"幂等"。

2. **兜底扫描（反向修正）**
   正向扫描处理"预约 → 设备状态"。
   但还有一种脏数据：设备是 busy，却没有任何进行中的预约
   （比如服务在预约进行中被强制中断、或者管理员手工改过状态）。
   所以需要第二步"反向扫描"：找出所有 busy 但无活跃预约的设备，恢复为 available。
   这种"正向处理 + 反向修正"的双向校验是数据一致性的常见手法。

**为什么加了分布式锁？**
如果部署了多个实例（uvicorn --workers 4），每个实例都会跑定时任务，
同一个任务并发执行会导致重复处理。
加锁后同一时刻只有一个实例在执行（这叫"分布式定时任务"）。
生产环境更专业的方案是用 APScheduler 的持久化 JobStore
或者专门的调度系统（如 XXL-Job、Celery Beat）。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import clear_equipment_cache
from app.core.enums import BookingStatus, EquipmentStatus
from app.core.lock import LockKey, RedisLock
from app.models.booking import Booking
from app.models.equipment import Equipment

logger = logging.getLogger(__name__)


class EquipmentSyncService:
    """设备状态同步服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def sync_equipment_status(self) -> dict:
        """
        执行一次设备状态同步。

        :return: 统计信息（便于日志和监控）
        """
        now = datetime.now().replace(microsecond=0)
        stats = {
            "scanned": 0,          # 扫描到的 approved 预约数
            "set_busy": 0,         # 置为使用中的设备数
            "set_available": 0,    # 恢复为可用的设备数
            "completed": 0,        # 标记完成的预约数
            "counted": 0,          # 累加预约次数的设备数
            "fixed_by_fallback": 0,  # 兜底扫描修正的设备数
        }

        try:
            # ==================== 第 1 步：正向处理 approved 预约 ====================
            stmt = select(Booking).where(Booking.status == BookingStatus.APPROVED.value)
            result = await self.db.execute(stmt)
            bookings = result.scalars().all()
            stats["scanned"] = len(bookings)

            # 记录状态发生变化的设备 id，循环结束后统一清缓存
            # （为什么不在循环里清？因为有 15 条预约可能涉及 3 台设备，
            #   循环里清会重复清同一个 key，做无用功）
            changed_equipment_ids: set[int] = set()

            for booking in bookings:
                start_dt = datetime.combine(booking.booking_date, booking.start_time)
                end_dt = datetime.combine(booking.booking_date, booking.end_time)

                # 还没到开始时间 → 什么都不做
                if now < start_dt:
                    continue

                # 查设备（排除维护中的设备 —— 维护状态不该被自动改动）
                equip_stmt = select(Equipment).where(
                    Equipment.id == booking.equipment_id,
                    Equipment.status != EquipmentStatus.MAINTENANCE.value,
                )
                equipment = (await self.db.execute(equip_stmt)).scalars().first()
                if equipment is None:
                    # 设备不存在或正在维护，跳过（但不影响其他预约的处理）
                    continue

                if start_dt <= now <= end_dt:
                    # ---------- 场景 A：预约进行中 → 设备置为 busy ----------
                    if equipment.status != EquipmentStatus.BUSY.value:
                        equipment.status = EquipmentStatus.BUSY.value
                        stats["set_busy"] += 1
                        changed_equipment_ids.add(equipment.id)

                elif now > end_dt:
                    # ---------- 场景 B：预约已结束 → 设备恢复 + 预约完成 ----------
                    if equipment.status == EquipmentStatus.BUSY.value:
                        equipment.status = EquipmentStatus.AVAILABLE.value
                        stats["set_available"] += 1
                        changed_equipment_ids.add(equipment.id)

                    booking.status = BookingStatus.COMPLETED.value
                    stats["completed"] += 1

                    # ========== 幂等累加预约次数（关键） ==========
                    # 先检查标记再累加：已统计过的绝不重复加
                    if not booking.is_counted:
                        equipment.booking_count = (equipment.booking_count or 0) + 1
                        booking.is_counted = 1
                        stats["counted"] += 1
                        changed_equipment_ids.add(equipment.id)

            # ==================== 第 2 步：兜底反向扫描 ====================
            # 找出所有 busy 但已经没有进行中预约的设备，恢复为 available。
            # 这一步是为了修正"预约被取消/服务中断/人工改错"导致的脏状态。
            busy_stmt = select(Equipment).where(Equipment.status == EquipmentStatus.BUSY.value)
            busy_equipments = (await self.db.execute(busy_stmt)).scalars().all()

            for equipment in busy_equipments:
                # 查这台设备有没有"正在进行中"的已通过预约
                # 条件：booking_date + start_time <= now <= booking_date + end_time
                # 这里用 time 类型比较 + 日期相等，避免用数据库函数导致索引失效
                active_stmt = select(func.count()).select_from(Booking).where(
                    Booking.equipment_id == equipment.id,
                    Booking.status == BookingStatus.APPROVED.value,
                    Booking.booking_date == now.date(),
                    Booking.start_time <= now.time(),
                    Booking.end_time > now.time(),
                )
                active_count = (await self.db.execute(active_stmt)).scalar() or 0

                if active_count == 0:
                    equipment.status = EquipmentStatus.AVAILABLE.value
                    stats["fixed_by_fallback"] += 1
                    changed_equipment_ids.add(equipment.id)

            # ==================== 第 3 步：提交并清缓存 ====================
            await self.db.commit()

            # 只有状态真正变化的设备才清缓存（避免无意义的 Redis 操作）
            for equipment_id in changed_equipment_ids:
                await clear_equipment_cache(equipment_id)

            total_changes = (
                stats["set_busy"] + stats["set_available"]
                + stats["completed"] + stats["fixed_by_fallback"]
            )
            if total_changes > 0:
                logger.info(
                    "设备状态同步完成 | 扫描 %d 条预约 | 变为使用中 %d | 恢复可用 %d | "
                    "标记完成 %d | 累加次数 %d | 兜底修正 %d",
                    stats["scanned"], stats["set_busy"], stats["set_available"],
                    stats["completed"], stats["counted"], stats["fixed_by_fallback"],
                )
            else:
                logger.debug("设备状态同步完成，无变化 | 扫描 %d 条预约", stats["scanned"])

        except Exception as exc:
            await self.db.rollback()
            logger.error("设备状态同步失败：%s", exc, exc_info=True)
            raise

        return stats


class TokenCleanupService:
    """过期 Token 清理服务"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def cleanup_expired_tokens(self, keep_days: int = 7) -> int:
        """
        清理过期 Token。

        为什么要留 keep_days 的缓冲而不是"过期就删"？
        因为保留一段时间的过期记录有助于安全审计
        （比如排查"某个账号在什么时候被登出过"）。
        真正的生产系统还会把审计日志单独归档。

        :param keep_days: 过期后保留多少天再删除
        :return: 删除的记录数
        """
        from app.core.security import utc_now
        from app.models.user import UserToken

        cutoff = utc_now() - timedelta(days=keep_days)

        stmt = select(func.count()).select_from(UserToken).where(UserToken.expires_at < cutoff)
        count = (await self.db.execute(stmt)).scalar() or 0

        if count > 0:
            from sqlalchemy import delete

            await self.db.execute(
                delete(UserToken).where(UserToken.expires_at < cutoff)
            )
            await self.db.commit()
            logger.info("清理过期 Token | 删除 %d 条（过期超过 %d 天）", count, keep_days)

        return count


# ==============================================================================
#  定时任务入口（供 scheduler 调用）
# ==============================================================================

async def run_equipment_sync() -> None:
    """
    定时任务入口：同步设备状态。

    自带分布式锁，防止多实例并发执行同一个任务。

    注意：这里自己创建数据库会话，因为定时任务不在请求上下文里，
    拿不到 FastAPI 依赖注入的 session。
    """
    from app.core.database import AsyncSessionLocal

    # 加锁：等 0 秒（不等待），拿不到锁说明别的实例正在执行，直接跳过
    lock = RedisLock(LockKey.equipment_sync(), timeout=55, wait_timeout=0)
    if not await lock.acquire():
        logger.debug("设备状态同步任务被其他实例持有，本次跳过")
        return

    try:
        async with AsyncSessionLocal() as db:
            service = EquipmentSyncService(db)
            await service.sync_equipment_status()
    except Exception as exc:
        logger.error("定时任务异常 | sync_equipment_status | %s", exc, exc_info=True)
    finally:
        # 必须释放锁，否则下一轮任务会被一直跳过
        await lock.release()


async def run_token_cleanup() -> None:
    """
    定时任务入口：清理过期 Token。

    每小时执行一次。频率不需要高 —— 清理是"维护性"操作，
    晚一点清理不会影响功能（认证时本来就会检查过期时间）。
    """
    from app.core.database import AsyncSessionLocal

    lock = RedisLock(LockKey.token_cleanup(), timeout=300, wait_timeout=0)
    if not await lock.acquire():
        return

    try:
        async with AsyncSessionLocal() as db:
            service = TokenCleanupService(db)
            await service.cleanup_expired_tokens(keep_days=7)
    except Exception as exc:
        logger.error("定时任务异常 | cleanup_expired_tokens | %s", exc, exc_info=True)
    finally:
        await lock.release()
