"""
数据库初始化脚本。

作用：
  1. 根据 ORM 模型创建所有表（Base.metadata.create_all）
  2. 插入初始数据：角色、用户、分类、实验室、设备、预约
  3. 生成/复制设备图片，并写入数据库

运行方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\init_db.py

可选参数：
    --drop       先删除所有表再重建（会清空数据，危险操作）
    --no-seed    只建表，不插入示例数据

为什么用脚本而不是 .sql 文件？
  用 ORM 的 create_all 建表，能保证「表结构和模型定义绝对一致」——
  原项目最大的隐患就是 SQL 脚本和 ORM 模型对不上
  （比如脚本里漏了 booking_count 字段，导致定时任务每次执行都失败）。
  用模型生成表结构，从根上消除了这种不一致。
"""
import argparse
import asyncio
import hashlib
import random
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# 把项目根目录加入 sys.path，这样脚本可以直接 import app.*
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402

import app.models  # noqa: F401,E402  必须导入，才能完成所有模型的映射注册
from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.core.enums import BookingStatus, EquipmentStatus, RoleCode, UserStatus  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models import (  # noqa: E402
    Booking,
    Equipment,
    EquipmentCategory,
    Laboratory,
    Role,
    User,
    UserRole,
)

# ====================== 初始数据定义 ======================

ROLES = [
    {"code": RoleCode.STUDENT.value, "name": "学生", "description": "普通学生用户，可浏览设备、提交预约"},
    {"code": RoleCode.TEACHER.value, "name": "教师", "description": "教师用户，可预约设备"},
    {"code": RoleCode.ADMIN.value, "name": "管理员", "description": "管理员，可审核预约、管理设备与用户"},
]

# 演示账号：密码统一在下面按角色设置
DEMO_USERS = [
    # (用户名, 密码, 姓名, 邮箱, 手机, 角色列表)
    ("admin", "Admin@123", "张管理员", "admin@lab.edu.cn", "13800000001", [RoleCode.ADMIN]),
    ("teacher1", "Teacher@123", "李教授", "li@lab.edu.cn", "13800000002", [RoleCode.TEACHER]),
    ("teacher2", "Teacher@123", "王副教授", "wang@lab.edu.cn", "13800000003", [RoleCode.TEACHER]),
    ("student1", "Student@123", "张同学", "zhang01@stu.edu.cn", "13900000001", [RoleCode.STUDENT]),
    ("student2", "Student@123", "李同学", "li02@stu.edu.cn", "13900000002", [RoleCode.STUDENT]),
    ("student3", "Student@123", "王同学", "wang03@stu.edu.cn", "13900000003", [RoleCode.STUDENT]),
    ("student4", "Student@123", "陈同学", "chen04@stu.edu.cn", "13900000004", [RoleCode.STUDENT]),
    ("student5", "Student@123", "刘同学", "liu05@stu.edu.cn", "13900000005", [RoleCode.STUDENT]),
    ("student6", "Student@123", "赵同学", "zhao06@stu.edu.cn", "13900000006", [RoleCode.STUDENT]),
]

CATEGORIES = [
    ("电子测量仪器", "示波器、频谱仪、信号发生器等电子测量设备"),
    ("计算机设备", "服务器、工作站、网络设备"),
    ("物理实验设备", "光学、力学、热学实验仪器"),
    ("化学实验设备", "分析仪器、反应装置"),
    ("机械工程设备", "数控机床、3D 打印机等加工设备"),
    ("生物实验设备", "离心机、PCR 仪、显微镜等"),
]

LABORATORIES = [
    ("电子实验室B201", "B栋2楼201", "电子测量与信号处理实验室", 30),
    ("电子实验室B202", "B栋2楼202", "电路基础实验室", 30),
    ("计算机实验室A101", "A栋1楼101", "计算机教学实验室", 60),
    ("物理实验室C301", "C栋3楼301", "物理教学实验室", 40),
    ("化学实验室D401", "D栋4楼401", "分析化学实验室", 25),
    ("机械实验室F601", "F栋6楼601", "机械加工实训中心", 20),
    ("生物实验室E501", "E栋5楼501", "生物医学实验室", 25),
]

# (名称, 型号, 序列号, 分类索引, 实验室索引, 状态, 采购日期, 价格, 描述, 主题色)
EQUIPMENTS = [
    ("数字示波器", "Keysight DSOX1204G", "EQ2026001", 0, 0, EquipmentStatus.AVAILABLE,
     date(2024, 3, 15), 45000.00, "四通道 200MHz 数字示波器，支持串行协议解码", "#409EFF"),
    ("频谱分析仪", "Keysight N9000B", "EQ2026002", 0, 0, EquipmentStatus.AVAILABLE,
     date(2024, 4, 20), 85000.00, "9kHz-26.5GHz 频谱分析仪", "#67C23A"),
    ("信号发生器", "R&S SMBV100A", "EQ2026003", 0, 0, EquipmentStatus.MAINTENANCE,
     date(2023, 11, 10), 95000.00, "矢量信号发生器，正在校准中", "#E6A23C"),
    ("可编程电子负载", "Chroma 63200A", "EQ2026004", 0, 1, EquipmentStatus.AVAILABLE,
     date(2023, 10, 30), 65000.00, "可编程直流电子负载", "#F56C6C"),
    ("数字万用表", "Fluke 87V", "EQ2026005", 0, 1, EquipmentStatus.AVAILABLE,
     date(2024, 5, 5), 3200.00, "高精度数字万用表", "#909399"),
    ("可编程交流电源", "Chroma 61500", "EQ2026006", 0, 1, EquipmentStatus.AVAILABLE,
     date(2023, 12, 20), 120000.00, "可编程交流电源，支持谐波模拟", "#67C23A"),
    ("混合域示波器", "Tektronix MDO3034", "EQ2026007", 0, 1, EquipmentStatus.AVAILABLE,
     date(2024, 2, 15), 55000.00, "混合域示波器，含频谱分析功能", "#409EFF"),
    ("激光干涉仪", "Agilent 5529A", "EQ2026008", 2, 3, EquipmentStatus.AVAILABLE,
     date(2023, 9, 18), 95000.00, "激光干涉测量仪，精度 1nm", "#E6A23C"),
    ("光栅光谱仪", "Ocean Insight QE65000", "EQ2026009", 2, 3, EquipmentStatus.AVAILABLE,
     date(2024, 1, 25), 45000.00, "高灵敏度光栅光谱仪", "#67C23A"),
    ("高速离心机", "Thermo Sorvall LYNX", "EQ2026010", 5, 6, EquipmentStatus.AVAILABLE,
     date(2024, 2, 10), 75000.00, "高速冷冻离心机，最高 25000rpm", "#F56C6C"),
    ("实时荧光PCR仪", "Bio-Rad CFX96", "EQ2026011", 5, 6, EquipmentStatus.AVAILABLE,
     date(2023, 12, 5), 55000.00, "实时荧光定量 PCR 仪", "#409EFF"),
    ("生物显微镜", "Olympus BX53", "EQ2026012", 5, 6, EquipmentStatus.AVAILABLE,
     date(2024, 3, 1), 35000.00, "研究级生物显微镜", "#909399"),
    ("数控车床", "Haas ST-20", "EQ2026013", 4, 5, EquipmentStatus.AVAILABLE,
     date(2023, 11, 20), 180000.00, "数控车床，最大加工直径 300mm", "#67C23A"),
    ("五轴加工中心", "DMG MORI DMU 50", "EQ2026014", 4, 5, EquipmentStatus.MAINTENANCE,
     date(2024, 1, 10), 250000.00, "五轴联动加工中心，刀具维护中", "#E6A23C"),
    ("工业级3D打印机", "Stratasys F370", "EQ2026015", 4, 5, EquipmentStatus.AVAILABLE,
     date(2023, 12, 25), 85000.00, "工业级 FDM 3D 打印机", "#409EFF"),
]

# 示例预约：(设备索引, 用户索引, 相对今天的天数, 开始, 结束, 用途, 状态)
#
# 说明（为什么要这么多条）：
#   1. 覆盖全部 5 种状态，方便前端把每种状态的样式都验证一遍
#   2. 每天的时段要"分散"（不同设备/不同时段），
#      否则测试脚本里随便挑个时段就会撞到冲突，
#      或者反过来因为冲突太多而测不出想测的东西
#   3. 每个学生用户分到的预约数要留有余量 ——
#      业务规则里"有效预约上限 10 条"，如果种子数据就占了 5 条，
#      测试时再建几条就撞上限了，会把真正的 bug 掩盖掉
#      （这一点是我在调试并发问题时真实踩到的坑）
DEMO_BOOKINGS = [
    # ---------- 已拒绝 ----------
    (10, 0, -8, time(15, 0), time(17, 0), "基因扩增实验：设备校准不合格，已拒绝",
     BookingStatus.REJECTED),
    (14, 1, -7, time(9, 0), time(11, 0), "五轴加工实验：设备维护中，已拒绝",
     BookingStatus.REJECTED),

    # ---------- 已完成（设备预约次数会累加）----------
    (0, 0, -6, time(9, 0), time(11, 0), "数字电路实验：测量信号波形", BookingStatus.COMPLETED),
    (1, 1, -5, time(14, 0), time(16, 0), "频谱分析实验：分析信号频谱特性", BookingStatus.COMPLETED),
    (3, 2, -4, time(10, 0), time(12, 0), "电源负载特性测试", BookingStatus.COMPLETED),
    (4, 3, -3, time(15, 0), time(17, 0), "电路参数测量实验", BookingStatus.COMPLETED),
    (7, 4, -2, time(9, 0), time(11, 0), "物理光学实验：激光干涉测量", BookingStatus.COMPLETED),
    (12, 0, -1, time(13, 0), time(15, 0), "机械加工实验：车床加工零件", BookingStatus.COMPLETED),

    # ---------- 已取消 ----------
    (9, 2, -3, time(9, 0), time(10, 0), "生物分离实验：临时有事取消", BookingStatus.CANCELLED),
    (6, 3, 5, time(13, 0), time(15, 0), "交流电源测试：调整到其他设备", BookingStatus.CANCELLED),

    # ---------- 已通过（未来，用于演示"使用中"和定时任务流转）----------
    (0, 1, 0, time(8, 0), time(10, 0), "课程实验：数字示波器基础操作", BookingStatus.APPROVED),
    (2, 2, 0, time(14, 0), time(16, 0), "信号发生器实践", BookingStatus.APPROVED),
    (6, 3, 1, time(10, 0), time(12, 0), "混合域测量实验", BookingStatus.APPROVED),
    (8, 4, 1, time(15, 0), time(17, 0), "光谱实验：材料成分分析", BookingStatus.APPROVED),

    # ---------- 待审核（覆盖多种时段，方便演示审核流程）----------
    (1, 0, 2, time(9, 0), time(11, 0), "频谱分析进阶实验", BookingStatus.PENDING),
    (3, 1, 3, time(10, 0), time(12, 0), "电子负载性能测试", BookingStatus.PENDING),
    (4, 2, 4, time(14, 0), time(16, 0), "万用表校准实验", BookingStatus.PENDING),
    (5, 3, 5, time(9, 0), time(11, 0), "交流电源谐波测试", BookingStatus.PENDING),
    (7, 4, 6, time(15, 0), time(17, 0), "激光干涉仪精度验证", BookingStatus.PENDING),
    (10, 0, 7, time(9, 0), time(11, 0), "PCR 扩增实验", BookingStatus.PENDING),
    (11, 1, 8, time(14, 0), time(16, 0), "显微观察实验", BookingStatus.PENDING),
    (13, 2, 9, time(10, 0), time(12, 0), "数控编程实训", BookingStatus.PENDING),
]


# ====================== 建表 ======================

async def create_tables(drop_first: bool = False) -> None:
    """创建所有表"""
    async with engine.begin() as conn:
        if drop_first:
            print("  [警告] 正在删除所有已存在的表...")
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    table_names = ", ".join(sorted(Base.metadata.tables.keys()))
    print(f"  [完成] 已创建 {len(Base.metadata.tables)} 张表：{table_names}")


# ====================== 插入数据 ======================

async def seed_data() -> None:
    """插入示例数据（幂等：已有数据则跳过）"""
    async with AsyncSessionLocal() as db:
        # ---------- 1. 角色 ----------
        existing = (await db.execute(select(Role))).scalars().all()
        if existing:
            print("  [跳过] 数据库中已有数据，不重复插入。")
            print("         如需重建，请执行： python scripts/init_db.py --drop")
            return

        role_map: dict[str, Role] = {}
        for item in ROLES:
            role = Role(code=item["code"], name=item["name"], description=item["description"],
                        created_at=datetime.now())
            db.add(role)
            role_map[item["code"]] = role
        await db.flush()  # flush 让 role.id 生成，但不提交事务
        print(f"  [完成] 插入 {len(ROLES)} 个角色")

        # ---------- 2. 用户（含角色分配） ----------
        user_map: dict[str, User] = {}
        for username, password, real_name, email, phone, role_codes in DEMO_USERS:
            user = User(
                username=username,
                password=get_password_hash(password),
                real_name=real_name,
                email=email,
                phone=phone,
                status=UserStatus.ACTIVE.value,
            )
            db.add(user)
            await db.flush()
            # 分配角色：显式操作中间表，而不是 user.roles.append()
            # （roles 关系是 viewonly，无法写入，这是有意设计）
            for code in role_codes:
                db.add(UserRole(user_id=user.id, role_id=role_map[code.value].id))
            user_map[username] = user
        await db.flush()
        print(f"  [完成] 插入 {len(DEMO_USERS)} 个用户并分配角色")

        # ---------- 3. 设备分类 ----------
        categories: list[EquipmentCategory] = []
        for idx, (name, desc) in enumerate(CATEGORIES):
            cat = EquipmentCategory(name=name, description=desc, sort_order=idx)
            db.add(cat)
            categories.append(cat)
        await db.flush()
        print(f"  [完成] 插入 {len(CATEGORIES)} 个设备分类")

        # ---------- 4. 实验室 ----------
        labs: list[Laboratory] = []
        for name, location, desc, capacity in LABORATORIES:
            lab = Laboratory(name=name, location=location, description=desc, capacity=capacity)
            db.add(lab)
            labs.append(lab)
        await db.flush()
        print(f"  [完成] 插入 {len(LABORATORIES)} 个实验室")

        # ---------- 5. 设备 ----------
        equipments: list[Equipment] = []
        for (name, model, serial, cat_idx, lab_idx, status, purchase, price,
             desc, color) in EQUIPMENTS:
            eq = Equipment(
                name=name,
                model=model,
                serial_number=serial,
                category_id=categories[cat_idx].id,
                lab_id=labs[lab_idx].id,
                status=status.value,
                purchase_date=purchase,
                price=price,
                description=desc,
                browse_count=random.randint(5, 200),
                booking_count=0,
                image=None,  # 图片由 generate_placeholder_images 单独处理
            )
            db.add(eq)
            equipments.append(eq)
        await db.flush()
        print(f"  [完成] 插入 {len(EQUIPMENTS)} 台设备")

        # ---------- 6. 示例预约 ----------
        today = date.today()
        student_usernames = ["student1", "student2", "student3", "student4",
                            "student5", "student6"]
        for eq_idx, user_idx, day_offset, start, end, purpose, status in DEMO_BOOKINGS:
            booking = Booking(
                user_id=user_map[student_usernames[user_idx % len(student_usernames)]].id,
                equipment_id=equipments[eq_idx].id,
                booking_date=today + timedelta(days=day_offset),
                start_time=start,
                end_time=end,
                purpose=purpose,
                notes=None,
                status=status.value,
                is_counted=1 if status == BookingStatus.COMPLETED else 0,
                audited_by=user_map["admin"].id if status != BookingStatus.PENDING else None,
                audited_at=datetime.now() - timedelta(days=1)
                if status != BookingStatus.PENDING else None,
                audit_note="同意使用" if status != BookingStatus.PENDING else None,
            )
            db.add(booking)
            # 已完成的预约计入设备预约次数
            if status == BookingStatus.COMPLETED:
                equipments[eq_idx].booking_count += 1

        await db.commit()
        print(f"  [完成] 插入 {len(DEMO_BOOKINGS)} 条示例预约")


# ====================== 图片处理 ======================

def make_placeholder_svg(name: str, color: str, serial: str) -> str:
    """
    生成一张 SVG 占位图。

    为什么用 SVG 而不是 PNG？
    - 纯文本，几行就能生成，不需要图像库（Pillow）和二进制操作
    - 矢量图，任意尺寸都清晰
    - 体积极小（几百字节）
    这是"没有真实照片时给系统一个像样的默认图"的实用方案。
    """
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{color}" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0.55"/>
    </linearGradient>
  </defs>
  <rect width="640" height="400" fill="url(#g)"/>
  <circle cx="540" cy="70" r="110" fill="#ffffff" opacity="0.10"/>
  <circle cx="90" cy="340" r="80" fill="#ffffff" opacity="0.08"/>
  <text x="50%" y="46%" text-anchor="middle" fill="#ffffff"
        font-family="Microsoft YaHei, PingFang SC, sans-serif" font-size="46" font-weight="bold">{name}</text>
  <text x="50%" y="60%" text-anchor="middle" fill="#ffffff" opacity="0.85"
        font-family="Consolas, monospace" font-size="24">{serial}</text>
</svg>
'''


async def assign_images(old_project_static: Path | None = None) -> None:
    """
    为设备分配图片。

    策略：
      1. 如果旧项目的 app/static/equipment 里有可用图片，按设备ID复制过来
         （这样能看到真实的设备照片，也验证了新的图片访问路径）
      2. 没有真实图片的设备，生成 SVG 占位图

    这样每台设备都有图，不会出现"图片显示不出来"的情况。
    """
    from app.core.config import BASE_DIR

    target_dir = BASE_DIR / "app" / "static" / "uploads" / "equipment" / "seed"
    target_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as db:
        equipments = (await db.execute(select(Equipment).order_by(Equipment.id))).scalars().all()

        copied = 0
        generated = 0

        for eq in equipments:
            image_name: str | None = None

            # 尝试从旧项目复制真实照片
            if old_project_static is not None:
                for ext in (".jpg", ".jpeg", ".png", ".webp"):
                    source = old_project_static / f"{eq.id}{ext}"
                    if source.is_file():
                        # 内容哈希做文件名，天然去重
                        digest = hashlib.md5(source.read_bytes()).hexdigest()[:12]
                        dest = target_dir / f"{digest}{ext}"
                        if not dest.exists():
                            shutil.copy2(source, dest)
                        image_name = dest.name
                        copied += 1
                        break

            # 没有真实照片 → 生成 SVG 占位图
            if image_name is None:
                color = "#409EFF"
                for item in EQUIPMENTS:
                    if item[2] == eq.serial_number:
                        color = item[9]
                        break
                svg = make_placeholder_svg(eq.name, color, eq.serial_number)
                image_name = f"{eq.serial_number}.svg"
                (target_dir / image_name).write_text(svg, encoding="utf-8")
                generated += 1

            # 数据库只存相对路径 —— 这是与前端的唯一契约
            eq.image = f"uploads/equipment/seed/{image_name}"

        await db.commit()
        print(f"  [完成] 图片处理：复制真实照片 {copied} 张，生成占位图 {generated} 张")


# ====================== 主流程 ======================

async def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 V2 数据库")
    parser.add_argument("--drop", action="store_true", help="先删除所有表再重建（会清空数据）")
    parser.add_argument("--no-seed", action="store_true", help="只建表，不插入示例数据")
    parser.add_argument(
        "--old-static",
        type=str,
        default=r"C:\code\LabBookingSystem\backend_fastapi\app\static\equipment",
        help="旧项目设备图片目录，用于复制真实照片",
    )
    args = parser.parse_args()

    print("=" * 66)
    print("  V2 数据库初始化")
    print("=" * 66)

    print("\n[1/4] 创建数据表")
    await create_tables(drop_first=args.drop)

    if args.no_seed:
        print("\n[跳过] --no-seed 指定不插入示例数据")
    else:
        print("\n[2/4] 插入示例数据")
        await seed_data()

        print("\n[3/4] 处理设备图片")
        old_static = Path(args.old_static) if args.old_static else None
        if old_static and old_static.is_dir():
            print(f"        旧图片目录存在，将尝试复制：{old_static}")
        else:
            print("        未找到旧图片目录，全部使用生成的占位图")
            old_static = None
        await assign_images(old_static)

    print("\n[4/4] 数据统计")
    async with AsyncSessionLocal() as db:
        for table, model in [
            ("角色", Role), ("用户", User), ("设备分类", EquipmentCategory),
            ("实验室", Laboratory), ("设备", Equipment), ("预约", Booking),
        ]:
            count = (await db.execute(select(model))).scalars().all()
            print(f"        {table}: {len(count)} 条")

    await engine.dispose()
    print("\n" + "=" * 66)
    print("  初始化完成")
    print("=" * 66)
    print("\n演示账号：")
    print("  管理员  admin     / Admin@123")
    print("  教师    teacher1  / Teacher@123")
    print("  学生    student1  / Student@123")
    print()


if __name__ == "__main__":
    asyncio.run(main())
