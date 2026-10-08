"""
设备收藏模型。

设计要点（面试可以讲）：
1. 用 (user_id, equipment_id) 复合唯一约束，从数据库层面杜绝重复收藏。
   原项目在应用层先查一遍"有没有收藏过"再插入，这是典型的
   check-then-act 竞态：两个并发请求都查到"没收藏"，然后都插入成功。
   加上唯一约束后，即使竞态发生，数据库也会拒绝第二条，
   应用层只需捕获 IntegrityError 并返回友好提示即可。

2. 不用自增 id 做业务语义，但保留 id 作为代理主键。
   为什么不用 (user_id, equipment_id) 直接做复合主键？
   因为收藏记录后续可能要扩展（加分组、加备注），
   用代理主键更利于演进，且前端传参更简单。
"""
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.equipment import Equipment
    from app.models.user import User


class EquipmentCollection(IdMixin, TimestampMixin, Base):
    """用户收藏的设备"""

    __tablename__ = "equipment_collections"
    __table_args__ = (
        # 核心约束：同一用户不能重复收藏同一设备
        UniqueConstraint("user_id", "equipment_id", name="uq_collection_user_equipment"),
        Index("idx_collection_user", "user_id"),
        {"comment": "设备收藏表"},
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID"
    )
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, comment="设备ID"
    )

    user: Mapped["User"] = relationship(back_populates="collections")
    equipment: Mapped["Equipment"] = relationship(back_populates="collections")

    def __repr__(self) -> str:
        return f"<Collection user={self.user_id} equipment={self.equipment_id}>"
