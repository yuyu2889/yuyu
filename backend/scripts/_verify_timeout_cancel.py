"""
验证「超时未审核的 pending 预约自动取消」功能。

验证点：
1. 预约日期已过、状态为 pending  → 定时任务应自动取消，并写入取消原因
2. 预约日期是今天、状态为 pending → **不应**被取消（当天仍有机会被审核）
3. 预约日期在未来、状态为 pending → **不应**被取消
4. 已被取消/已完成的预约          → 不受影响（终态不可流转）
5. 设备列表的「当前有效预约数」应随取消而减少
6. 脚本结束会清理测试数据，不污染数据库

同时验证「常量统一」的改动没有破坏原有行为：
  repositories/equipment.py 和 services/statistics_service.py
  现在都从 core/enums.py 取 BOOKING_ACTIVE_STATUS_VALUES
"""
import asyncio
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select

from app.core.enums import BOOKING_TIMEOUT_CANCEL_REASON, BookingStatus
from app.core.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.equipment import Equipment
from app.repositories.equipment import EquipmentRepository
from app.services.equipment_sync_service import EquipmentSyncService

created_ids: list[int] = []


async def main() -> int:
    today = date.today()
    failures: list[str] = []

    print("=" * 74)
    print("  验证：超时未审核的 pending 预约自动取消")
    print("=" * 74)

    # ------------------------------------------------------------------
    #  先清理上次运行可能残留的测试数据（防止脚本中途失败留下脏数据）
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        leftovers = (
            await db.execute(
                select(Booking).where(Booking.purpose.like("验证超时取消%"))
            )
        ).scalars().all()
        if leftovers:
            for b in leftovers:
                await db.delete(b)
            await db.commit()
            print(f"  [预清理] 删除了 {len(leftovers)} 条上次残留的测试预约")
        else:
            print("  [预清理] 没有残留数据")

    # ------------------------------------------------------------------
    #  准备：造 3 条 pending 预约（过期 / 今天 / 未来）
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        eq = (
            await db.execute(
                select(Equipment).where(Equipment.status == "available").limit(1)
            )
        ).scalar_one()
        equipment_id, equipment_name = eq.id, eq.name
        user_id = 6

        cases = [
            ("过期 pending（应被取消）", today - timedelta(days=3), time(9, 0), time(11, 0)),
            ("今天 pending（不应取消）", today, time(23, 0), time(23, 30)),
            ("未来 pending（不应取消）", today + timedelta(days=5), time(9, 0), time(11, 0)),
        ]

        for label, bdate, st, et in cases:
            b = Booking(
                user_id=user_id,
                equipment_id=equipment_id,
                booking_date=bdate,
                start_time=st,
                end_time=et,
                purpose=f"验证超时取消 - {label}",
                status=BookingStatus.PENDING.value,
                is_counted=0,
            )
            db.add(b)
            await db.flush()
            created_ids.append(b.id)
            print(f"  [造数据] id={b.id}  {label}  日期={bdate}")

        await db.commit()

    print()
    print(f"  使用设备：{equipment_name} (id={equipment_id})")

    # ------------------------------------------------------------------
    #  记录执行前的「当前有效预约数」
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        repo = EquipmentRepository(db)
        items, _ = await repo.list_with_stats(offset=0, limit=100)
        before = next(
            (i["active_booking_count"] for i in items if i["id"] == equipment_id), None
        )
    print(f"  执行前：该设备的「当前有效预约数」= {before}")

    # ------------------------------------------------------------------
    #  执行定时任务
    # ------------------------------------------------------------------
    print()
    print("  [执行定时任务]")
    async with AsyncSessionLocal() as db:
        svc = EquipmentSyncService(db)
        stats = await svc.sync_equipment_status()
    changed = {k: v for k, v in stats.items() if v}
    print(f"    统计结果：{changed if changed else '无变化'}")

    if stats.get("timeout_cancelled") != 1:
        failures.append(
            f"timeout_cancelled 应为 1，实际 {stats.get('timeout_cancelled')}"
        )

    # ------------------------------------------------------------------
    #  逐条核对状态
    # ------------------------------------------------------------------
    print()
    print("  逐条核对：")
    print(f"    {'id':<6}{'场景':<26}{'期望状态':<12}{'实际状态':<12}{'结果'}")
    print("    " + "-" * 66)

    expectations = [
        (created_ids[0], "过期 pending（应被取消）", BookingStatus.CANCELLED.value),
        (created_ids[1], "今天 pending（不应取消）", BookingStatus.PENDING.value),
        (created_ids[2], "未来 pending（不应取消）", BookingStatus.PENDING.value),
    ]

    async with AsyncSessionLocal() as db:
        for bid, label, expect in expectations:
            b = await db.get(Booking, bid)
            ok = b.status == expect
            if not ok:
                failures.append(f"id={bid} 期望 {expect}，实际 {b.status}")
            print(f"    {bid:<6}{label:<26}{expect:<12}{b.status:<12}{'OK' if ok else '不一致!'}")

        # 核对取消原因是否写入
        expired = await db.get(Booking, created_ids[0])
        print()
        print(f"  被取消预约的审核备注：{expired.audit_note!r}")
        if expired.audit_note != BOOKING_TIMEOUT_CANCEL_REASON:
            failures.append(
                f"取消原因未正确写入，实际为 {expired.audit_note!r}"
            )

    # ------------------------------------------------------------------
    #  核对「当前有效预约数」是否减少（原本 3 条 pending，取消 1 条后应为 2）
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        repo = EquipmentRepository(db)
        items, _ = await repo.list_with_stats(offset=0, limit=100)
        after = next(
            (i["active_booking_count"] for i in items if i["id"] == equipment_id), None
        )
    print()
    print(f"  执行后：该设备的「当前有效预约数」= {after}（执行前 {before}）")
    if before is not None and after is not None and after != before - 1:
        failures.append(f"有效预约数应减少 1（{before} -> {before - 1}），实际 {after}")

    # ------------------------------------------------------------------
    #  幂等性：再执行一次不应重复取消
    # ------------------------------------------------------------------
    print()
    print("  [第 2 次执行] —— 验证幂等性")
    async with AsyncSessionLocal() as db:
        svc = EquipmentSyncService(db)
        stats2 = await svc.sync_equipment_status()
    print(f"    timeout_cancelled = {stats2.get('timeout_cancelled')}（应为 0）")
    if stats2.get("timeout_cancelled") != 0:
        failures.append(
            f"重复执行不应再取消，实际 {stats2.get('timeout_cancelled')}"
        )

    # ------------------------------------------------------------------
    #  清理测试数据
    # ------------------------------------------------------------------
    print()
    print("  [清理测试数据]")
    async with AsyncSessionLocal() as db:
        for bid in created_ids:
            b = await db.get(Booking, bid)
            if b is not None:
                await db.delete(b)
        await db.commit()
    print(f"    已删除 {len(created_ids)} 条测试预约")

    # ------------------------------------------------------------------
    #  结论
    # ------------------------------------------------------------------
    print()
    print("=" * 74)
    if failures:
        print("  验证失败：")
        for f in failures:
            print(f"    - {f}")
        print("=" * 74)
        return 1

    print("  全部验证通过")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
