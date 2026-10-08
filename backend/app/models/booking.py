"""
预约模型。

设计要点（面试可以讲，这是全项目最重要的表）：

1. 审计字段独立成列，不再塞进 notes。
   原项目的做法是：审核时把"【管理员xxx审核备注：yyy】"拼接进 notes 字段
   （见原 routers/booking.py）。问题有三个：
   - 备注里混进了用户原始填写的备注，无法区分
   - 无法按"谁审的"查询/统计
   - 想改格式就得写字符串解析
   V2 拆成 audited_by / audited_at / audit_note 三列，语义清晰、可查询、可审计。

2. is_counted 是幂等标记，防定时任务重复计数。
   定时任务每 60 秒跑一次，如果只是"预约结束就 booking_count + 1"，
   那么任务重启、或同一条数据被扫到两次，就会把同一次预约算多次。
   加上 is_counted 后，先判断标记再累加，保证「最多计入一次」。
   这是「幂等（idempotent）」思维在业务里的典型应用。

3. 为什么用 (equipment_id, booking_date, status) 联合索引？
   冲突检测的 SQL 是：
     WHERE equipment_id = ? AND booking_date = ? AND status IN ('pending','approved')
   联合索引的字段顺序按「等值条件在前、范围条件在后」排列，
   这三个字段都是等值条件，所以能把候选行缩到极少。
   如果只建 (equipment_id) 单列索引，同设备的历史预约都会被扫到。
"""
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import BOOKING_ACTIVE_STATUSES, BookingStatus
from app.models.base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.equipment import Equipment
    from app.models.user import User


class Booking(IdMixin, TimestampMixin, Base):
    """预约记录表"""

    __tablename__ = "bookings"
    __table_args__ = (
        # 冲突检测专用联合索引（最重要的索引）
        Index("idx_booking_equip_date_status", "equipment_id", "booking_date", "status"),
        # 「我的预约」按用户查
        Index("idx_booking_user_status", "user_id", "status"),
        # 定时任务扫描：找所有 approved 的预约
        Index("idx_booking_status", "status"),
        # 统计近 7 天趋势时按创建时间分组
        Index("idx_booking_created_at", "created_at"),
        {"comment": "预约记录表"},
    )

    # ---------- 外键 ----------
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="预约人ID"
    )
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, comment="设备ID"
    )

    # ---------- 时间段 ----------
    booking_date: Mapped[date] = mapped_column(Date, nullable=False, comment="预约日期")
    start_time: Mapped[time] = mapped_column(Time, nullable=False, comment="开始时间")
    end_time: Mapped[time] = mapped_column(Time, nullable=False, comment="结束时间")

    # ---------- 内容 ----------
    purpose: Mapped[str] = mapped_column(Text, nullable=False, comment="使用目的")
    notes: Mapped[Optional[str]] = mapped_column(Text, comment="用户备注")

    # ---------- 状态 ----------
    status: Mapped[str] = mapped_column(
        SAEnum(BookingStatus, values_callable=lambda e: [x.value for x in e], native_enum=True),
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
        nullable=False,
        comment="预约状态：pending/approved/rejected/cancelled/completed",
    )

    # ---------- 审核信息（独立成列，不再拼进 notes） ----------
    audited_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), comment="审核人ID"
    )
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="审核时间")
    audit_note: Mapped[Optional[str]] = mapped_column(String(500), comment="审核备注")

    # ---------- 幂等标记 ----------
    is_counted: Mapped[bool] = mapped_column(
        Integer,  # 用 Integer 存 0/1，兼容性比 Boolean 好
        default=0,
        server_default="0",
        nullable=False,
        comment="是否已计入设备预约次数：0-未计入 1-已计入",
    )

    # ---------- 关系 ----------
    # 注意：bookings 表有两个指向 users 的外键（user_id=预约人、audited_by=审核人），
    # 所以每条通到 User 的关系都必须显式指定 foreign_keys，
    # 否则 SQLAlchemy 会抛 AmbiguousForeignKeysError。
    # 这类报错在"一张表有多个外键指向同一张表"时必然出现，是常见坑。
    user: Mapped["User"] = relationship(
        back_populates="bookings",
        foreign_keys=[user_id],
        lazy="select",
    )
    equipment: Mapped["Equipment"] = relationship(
        back_populates="bookings",
        lazy="select",
    )
    auditor: Mapped[Optional["User"]] = relationship(
        foreign_keys=[audited_by],
        lazy="select",
    )

    # ---------- 业务方法（把"状态判断"收敛到模型里） ----------

    @property
    def is_active(self) -> bool:
        """是否属于"占用设备时间段"的有效状态"""
        return self.status in [s.value for s in BOOKING_ACTIVE_STATUSES]

    @property
    def can_be_cancelled(self) -> bool:
        """是否允许取消：只有待审核和已通过可以取消"""
        return self.status in (BookingStatus.PENDING.value, BookingStatus.APPROVED.value)

    @property
    def can_be_audited(self) -> bool:
        """是否允许审核：只有待审核状态"""
        return self.status == BookingStatus.PENDING.value

    @property
    def start_datetime(self) -> datetime:
        """把日期和时间拼成完整的 datetime，用于时间比较"""
        return datetime.combine(self.booking_date, self.start_time)

    @property
    def end_datetime(self) -> datetime:
        return datetime.combine(self.booking_date, self.end_time)

    def __repr__(self) -> str:
        return (
            f"<Booking id={self.id} equipment={self.equipment_id} "
            f"{self.booking_date} {self.start_time}-{self.end_time} {self.status}>"
        )
