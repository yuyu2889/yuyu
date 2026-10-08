"""
设备、设备分类、实验室模型。

设计要点（面试可以讲）：
1. 外键 ondelete 的语义必须和应用层逻辑一致。
   原项目的问题：数据库外键写的是 ON DELETE SET NULL，
   但 ORM 关系写的是 cascade="all, delete-orphan"（级联删除子记录）——
   两套语义完全相反，删分类时行为取决于走哪条路径，这是隐患。
   V2 明确采用 RESTRICT：分类/实验室下还有设备时，数据库直接禁止删除，
   从机制上防止误删导致数据悬挂。

2. description 用 Text 而不是 String(500)。
   原项目模型写 String(500)、数据库写 TEXT，两边不一致。
   V2 统一为 Text，不设无意义的上限（真正的长度限制放在接口层校验，
   因为「数据库能存多少」和「业务允许输入多少」是两件事）。

3. browse_count / booking_count 这类「统计冗余字段」要不要存？
   反范式设计：每次查设备都要 COUNT 一遍预约表成本高，
   所以在设备表上冗余一个计数。代价是必须保证它和真实数据一致——
   原项目靠定时任务在预约结束时 +1，并且用 is_counted 标记防重复计数。
   V2 保留这个设计，但把更新逻辑收敛到 service 层一处。
"""
from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import EquipmentStatus
from app.models.base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.booking import Booking
    from app.models.collection import EquipmentCollection


class EquipmentCategory(IdMixin, TimestampMixin, Base):
    """设备分类，如「电子测量仪器」"""

    __tablename__ = "equipment_categories"

    name: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="分类名称"
    )
    description: Mapped[Optional[str]] = mapped_column(String(200), comment="分类描述")
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="排序值，越小越靠前"
    )

    equipments: Mapped[List["Equipment"]] = relationship(
        back_populates="category",
        # 不用 delete-orphan：分类下还有设备时不允许删除由数据库的 RESTRICT 保证，
        # ORM 这层只需被动跟随
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<EquipmentCategory {self.name}>"


class Laboratory(IdMixin, TimestampMixin, Base):
    """实验室，如「电子实验室B201」"""

    __tablename__ = "laboratories"

    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="实验室名称")
    location: Mapped[str] = mapped_column(String(100), nullable=False, comment="位置")
    description: Mapped[Optional[str]] = mapped_column(String(200), comment="描述")
    capacity: Mapped[Optional[int]] = mapped_column(Integer, comment="可容纳人数")

    equipments: Mapped[List["Equipment"]] = relationship(
        back_populates="lab",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Laboratory {self.name}>"


class Equipment(IdMixin, TimestampMixin, Base):
    """设备表"""

    __tablename__ = "equipment"
    __table_args__ = (
        # 按状态筛选是最常见的查询（查所有可用设备）
        Index("idx_equipment_status", "status"),
        # 按分类/实验室筛选
        Index("idx_equipment_category", "category_id"),
        Index("idx_equipment_lab", "lab_id"),
        # 热门设备排行按浏览量倒序
        Index("idx_equipment_browse_count", "browse_count"),
        {"comment": "设备表"},
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="设备名称")
    model: Mapped[str] = mapped_column(String(100), nullable=False, comment="设备型号")
    serial_number: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="序列号（唯一）"
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_categories.id", ondelete="RESTRICT"), comment="分类ID"
    )
    lab_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("laboratories.id", ondelete="RESTRICT"), comment="实验室ID"
    )
    status: Mapped[str] = mapped_column(
        SAEnum(EquipmentStatus, values_callable=lambda e: [x.value for x in e], native_enum=True),
        default=EquipmentStatus.AVAILABLE,
        server_default=EquipmentStatus.AVAILABLE.value,
        nullable=False,
        comment="设备状态：available/busy/maintenance",
    )
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, comment="采购日期")
    price: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2), comment="价格（元）"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, comment="设备描述")

    # ---------- 冗余统计字段 ----------
    browse_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="浏览量"
    )
    booking_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False,
        comment="累计完成预约次数（由定时任务在预约结束时 +1）",
    )

    # ---------- 图片 ----------
    # 只存相对路径，如 uploads/equipment/2026/10/xxx.jpg
    # 前端统一用 API_BASE + "/static/" + image 拼接，不做任何规则推断
    image: Mapped[Optional[str]] = mapped_column(
        String(255), comment="设备图片相对路径"
    )

    # ---------- 关系 ----------
    category: Mapped[Optional["EquipmentCategory"]] = relationship(
        back_populates="equipments",
        # 首页设备列表每次都要显示分类名，用 join 一次查出来，
        # 避免「查 20 台设备再查 20 次分类」的 N+1 问题
        lazy="joined",
    )
    lab: Mapped[Optional["Laboratory"]] = relationship(
        back_populates="equipments",
        lazy="joined",
    )
    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="equipment",
        passive_deletes=True,
    )
    collections: Mapped[List["EquipmentCollection"]] = relationship(
        back_populates="equipment",
        passive_deletes=True,
    )

    # ---------- 业务方法 ----------
    @property
    def is_available(self) -> bool:
        """是否可被预约"""
        return self.status == EquipmentStatus.AVAILABLE

    @property
    def is_maintenance(self) -> bool:
        return self.status == EquipmentStatus.MAINTENANCE

    def __repr__(self) -> str:
        return f"<Equipment {self.name}({self.serial_number}) status={self.status}>"
