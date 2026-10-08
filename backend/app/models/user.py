"""
用户、角色、Token 模型。

设计要点（面试可以讲）：
1. 角色用「多对多 + 中间表」而不是在 users 表里加一个 role_id 字段。
   为什么？因为真实场景一个用户可能同时是「教师」和「管理员」。
   你原项目的数据库文档里其实纠结过这一点（文档里写了"简化成一个 role_id"），
   但实际建表时又保留了 user_roles 中间表 —— 这就是设计不一致。
   V2 明确采用多对多，并且用「角色编码」而不是「自增 id」来判定权限，
   这样即使数据库重建、id 变化，代码也不用改。

2. roles 关系设为 viewonly=True。
   原因：角色变更统一走「显式操作 user_roles 表」的接口，
   不允许通过 user.roles.append() 这种隐式方式改，
   避免权限变更没有日志、没有校验。viewonly 从机制上禁止了隐式写入。

3. UserToken 上加了唯一索引（user_id），实现「一个用户同时只有一个有效 Token」。
   这是有意的产品决策：换设备登录会把旧设备踢下线。
   如果业务需要多设备同时在线，就去掉这个唯一索引，改成多行存储。

4. 定义了两个 __table_args__ 里没有的东西值得注意：
   - username / email / phone 都有唯一约束（防重复注册）
   - expire_at 上有索引（定时清理过期 Token 时会全表扫这个字段）
"""
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import RoleCode, UserStatus
from app.models.base import IdMixin, TimestampMixin

if TYPE_CHECKING:  # 仅用于类型提示，避免运行时循环导入
    from app.models.booking import Booking
    from app.models.collection import EquipmentCollection


class UserRole(Base):
    """
    用户-角色关联表（多对多中间表）。

    复合主键 (user_id, role_id) 本身就能防止重复分配同一个角色，
    不需要额外的唯一约束。
    """

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="用户ID",
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        comment="角色ID",
    )

    user: Mapped["User"] = relationship(back_populates="user_roles")
    role: Mapped["Role"] = relationship(back_populates="user_roles")


class Role(IdMixin, Base):
    """角色表"""

    __tablename__ = "roles"

    # 用 role.code 判定权限，而不是 role.id
    code: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        comment="角色编码：student/teacher/admin",
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色名称（中文）")
    description: Mapped[Optional[str]] = mapped_column(String(200), comment="角色描述")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="创建时间")

    user_roles: Mapped[List["UserRole"]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class User(IdMixin, TimestampMixin, Base):
    """用户表"""

    __tablename__ = "users"
    __table_args__ = (
        Index("idx_user_status", "status"),
        {"comment": "用户表"},
    )

    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="用户名（登录用）"
    )
    password: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="密码哈希（bcrypt，含盐）"
    )
    email: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="邮箱（唯一）"
    )
    phone: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, comment="手机号（唯一）"
    )
    real_name: Mapped[str] = mapped_column(String(50), nullable=False, comment="真实姓名")
    status: Mapped[str] = mapped_column(
        SAEnum(UserStatus, values_callable=lambda e: [x.value for x in e], native_enum=True),
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        nullable=False,
        comment="账号状态：active/disabled",
    )

    # ---------- 关系 ----------
    user_roles: Mapped[List["UserRole"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    roles: Mapped[List["Role"]] = relationship(
        secondary="user_roles",
        # primaryjoin / secondaryjoin 必须显式写出：
        # 因为 User 和 Role 之间存在多条潜在的外键路径
        #（Role 通过 user_roles 关联 User，User 又通过 bookings 关联别的表），
        # 不写清楚 SQLAlchemy 会报 AmbiguousForeignKeysError。
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        lazy="selectin",   # 查用户时顺带把角色查出来，避免 N+1
        viewonly=True,     # 只读，禁止隐式写入（角色变更走专门接口）
    )

    tokens: Mapped[List["UserToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,  # 依赖数据库的 ON DELETE CASCADE，不在 Python 侧逐条删
        # 必须显式指定：bookings 表有两个外键指向 users
        #（user_id 是预约人、audited_by 是审核人），不指定会报 AmbiguousForeignKeysError。
        # 这里用字符串是因为 Booking 类在另一个文件，避免循环导入。
        foreign_keys="Booking.user_id",
    )

    collections: Mapped[List["EquipmentCollection"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ---------- 业务方法 ----------
    def has_role(self, code: RoleCode) -> bool:
        """
        判断用户是否拥有某个角色。

        注意这里比较的是 role.code 而不是 role.id —— 这是 V2 相对原项目的
        改进点之一。原项目硬编码 role.id == 2 来判断管理员，
        一旦角色表数据重建、id 变化，权限判断就全错。
        """
        return any(r.code == code.value for r in self.roles)

    @property
    def is_admin(self) -> bool:
        return self.has_role(RoleCode.ADMIN)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    @property
    def role_codes(self) -> List[str]:
        return [r.code for r in self.roles]

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class UserToken(IdMixin, Base):
    """
    用户 Token 表（本项目的主认证方案）。

    为什么选它而不是 JWT？（面试高频问题，准备好这个答案）
    ┌────────────┬──────────────────────┬─────────────────────────┐
    │            │ UUID Token（本方案）  │ JWT                     │
    ├────────────┼──────────────────────┼─────────────────────────┤
    │ 服务端存储  │ 需要（一张表）        │ 不需要                  │
    │ 即时撤销    │ 改个字段就行          │ 必须维护黑名单          │
    │ 续期        │ 更新过期时间即可      │ 必须重新签发            │
    │ 每次请求开销│ 一次数据库查询        │ 纯计算，无 IO           │
    │ 水平扩展    │ 多实例共享同一张表即可 │ 天然无状态，更易扩展    │
    └────────────┴──────────────────────┴─────────────────────────┘
    本项目选 UUID Token，因为「管理员禁用用户后要立刻踢下线」「改密码后
    旧 Token 立即失效」这两个需求用 JWT 实现反而更复杂。
    代价是每次请求多一次数据库查询（走主键/唯一索引，开销很小）。
    """

    __tablename__ = "user_tokens"
    __table_args__ = (
        # 一个用户只保留一个有效 Token：换设备登录会把旧设备踢下线
        UniqueConstraint("user_id", name="uq_user_token_user_id"),
        Index("idx_token_expires", "expires_at"),
        {"comment": "用户登录令牌表"},
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="用户ID"
    )
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="令牌值（UUID4）"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="过期时间（UTC）"
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
        comment="是否已撤销：登出/改密码时置为 1",
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, comment="最后使用时间（用于统计活跃用户）"
    )

    user: Mapped["User"] = relationship(back_populates="tokens")

    def __repr__(self) -> str:
        return f"<UserToken user_id={self.user_id} revoked={self.is_revoked}>"
