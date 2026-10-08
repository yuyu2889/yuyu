"""
验证常量统一后，「设备列表」和「统计页」的当前有效预约数完全一致。

这两个值原本由两处独立代码算出：
  - repositories/equipment.py       （设备列表用）
  - services/statistics_service.py  （统计页用）
它们曾经各自写死状态列表。统一到 core/enums.py 之后，
必须验证两边结果一致 —— 否则就是「同一个指标在不同页面显示不同数字」。
"""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import AsyncSessionLocal
from app.repositories.equipment import EquipmentRepository
from app.services.statistics_service import StatisticsService


async def main() -> int:
    async with AsyncSessionLocal() as db:
        # 口径 1：设备列表用的（list_with_stats）
        eq_repo = EquipmentRepository(db)
        items, total = await eq_repo.list_with_stats(offset=0, limit=100)
        list_map = {i["id"]: i["active_booking_count"] for i in items}

        # 口径 2：统计页用的（get_equipment_statistics）
        svc = StatisticsService(db)
        stats = await svc.get_equipment_statistics(limit=100, order_by="browse_count")
        stat_map = {
            i["equipment_id"]: i["active_booking_count"]
            for i in stats["equipment_ranking"]
        }

    print("=" * 70)
    print("  对比两个模块算出的「当前有效预约数」")
    print("=" * 70)
    print()
    print(f"  设备列表接口返回 {len(list_map)} 台，统计接口返回 {len(stat_map)} 台")
    print()

    mismatches = []
    for eid in sorted(set(list_map) | set(stat_map)):
        a = list_map.get(eid)
        b = stat_map.get(eid)
        flag = "" if a == b else "  <- 不一致！"
        if a != b:
            mismatches.append((eid, a, b))
        print(f"    设备 {eid:>3}  列表={str(a):>4}  统计={str(b):>4}{flag}")

    print()
    print("=" * 70)
    if mismatches:
        print(f"  发现 {len(mismatches)} 处不一致：")
        for eid, a, b in mismatches:
            print(f"    设备 {eid}: 列表={a}, 统计={b}")
        print("=" * 70)
        return 1

    print("  两个模块结果完全一致 —— 常量统一生效")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
