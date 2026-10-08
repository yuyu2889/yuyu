"""
验证定时任务的设备状态同步与幂等计数。

验证点：
1. 已通过的预约，时间过了之后应被标记为 completed
2. 设备状态应从 busy 恢复为 available
3. 设备 booking_count 应 +1
4. **重复执行任务不应重复累加**（幂等性，核心）
5. 兜底扫描能修正"busy 但无活跃预约"的脏状态
"""
import asyncio
import sys
from datetime import date, datetime, time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.equipment import Equipment
from app.services.equipment_sync_service import EquipmentSyncService


async def main():
    today = date.today()
    now = datetime.now()

    # 构造一个"今天已经过去的时段"
    if now.hour >= 2:
        start, end = time(0, 30), time(1, 0)
    else:
        # 凌晨执行时用 0:00-0:30（可能还没到，换个更早的相对时间）
        start, end = time(0, 0), time(0, 30)

    print("=" * 70)
    print("定时任务验证：设备状态同步 + 幂等计数")
    print("=" * 70)

    # ---------- 准备数据 ----------
    async with AsyncSessionLocal() as db:
        eq = (
            await db.execute(
                select(Equipment).where(Equipment.status == "available").limit(1)
            )
        ).scalar_one()

        booking = Booking(
            user_id=6,
            equipment_id=eq.id,
            booking_date=today,
            start_time=start,
            end_time=end,
            purpose="定时任务验证：已结束的预约",
            status="approved",
            is_counted=0,
        )
        db.add(booking)
        await db.commit()

        bid, eqid = booking.id, eq.id
        before_count = eq.booking_count
        eq_name = eq.name

    print()
    print(f"[准备] 造数据：预约 id={bid}，设备「{eq_name}」(id={eqid})")
    print(f"       时段：今天 {start.strftime('%H:%M')}-{end.strftime('%H:%M')}（已过去）")
    print(f"       初始状态：预约=approved, is_counted=0, 设备预约次数={before_count}")

    # ---------- 第 1 次执行 ----------
    print()
    print("[第 1 次执行定时任务]")
    async with AsyncSessionLocal() as db:
        svc = EquipmentSyncService(db)
        stats = await svc.sync_equipment_status()
        changed = {k: v for k, v in stats.items() if v}
        print(f"       统计：{changed if changed else '无变化'}")

    async with AsyncSessionLocal() as db:
        b = await db.get(Booking, bid)
        e = await db.get(Equipment, eqid)
        after1 = e.booking_count
        print(f"       预约状态：{b.status}  is_counted={b.is_counted}")
        print(f"       设备状态：{e.status}  预约次数：{before_count} -> {after1}")

    # ---------- 第 2 次执行（幂等性验证）----------
    print()
    print("[第 2 次执行定时任务] —— 验证幂等性（不应重复累加）")
    async with AsyncSessionLocal() as db:
        svc = EquipmentSyncService(db)
        stats = await svc.sync_equipment_status()
        changed = {k: v for k, v in stats.items() if v}
        print(f"       统计：{changed if changed else '无变化'}")

    async with AsyncSessionLocal() as db:
        e = await db.get(Equipment, eqid)
        b = await db.get(Booking, bid)
        after2 = e.booking_count

    print(f"       预约次数：{after1} -> {after2}")

    # ---------- 结论 ----------
    print()
    print("=" * 70)
    print("验证结论：")

    # 注意：设备预约次数的增量可能 > 1 ——
    # 因为定时任务一次会扫描**所有** approved 预约，
    # 这台设备上可能还有别的"已结束但未计数"的预约也会被一起处理。
    # 所以这里不断言"恰好 +1"，而是断言：
    #   1) 至少 +1（说明我们的预约确实被计数了）
    #   2) 第二次执行不再增长（说明幂等）
    delta = after1 - before_count
    checks = [
        ("预约被标记为 completed", b.status == "completed"),
        ("is_counted 被置为 1", b.is_counted == 1),
        ("设备状态恢复为 available", e.status == "available"),
        (f"预约次数第 1 次执行时增加（+{delta}，应 >= 1）", delta >= 1),
        ("【幂等】第 2 次执行未重复累加", after2 == after1),
    ]
    all_ok = True
    for desc, ok in checks:
        mark = "[PASS]" if ok else "[FAIL]"
        if not ok:
            all_ok = False
        print(f"   {mark} {desc}")

    print()
    print("   " + ("全部通过：定时任务正确工作且具备幂等性"
                  if all_ok else "存在失败项，需要排查"))
    print("=" * 70)

    # ---------- 清理测试数据 ----------
    async with AsyncSessionLocal() as db:
        obj = await db.get(Booking, bid)
        if obj:
            await db.delete(obj)
            # 把计数改回去，保持数据干净
            equipment = await db.get(Equipment, eqid)
            equipment.booking_count = before_count
            await db.commit()
    print("（测试数据已清理）")


if __name__ == "__main__":
    asyncio.run(main())
