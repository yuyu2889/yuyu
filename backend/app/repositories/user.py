"""
用户与 Token 的数据访问。

设计要点（面试可以讲）：
1. 为什么「查用户列表」要用 join + 条件聚合，而不是逐个用户查收藏数？
   后者是典型的 N+1 查询：查 10 个用户就发 11 条 SQL。
   用一次 join 把所有数据带回来，是列表接口的标准优化手法。

2. selectinload 和 joinedload 的区别（面试高频）：
   - joinedload：用 LEFT JOIN 一次查回来。适合「一对多」里每边数据量小的情况，
     但如果一对多的"多"很多，join 会让结果集膨胀（笛卡尔积）。
   - selectinload：先查主表，再用 WHERE id IN (...) 查关联表（共 2 条 SQL）。
     适合「多」的数据量中等或偏大的情况，也避免了 join 膨胀。
   本项目：用户查角色用 selectinload（角色通常 1-3 个，但 join 会和收藏数聚合冲突）。
"""
from typing import List, Optional, Sequence

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.models.collection import EquipmentCollection
from app.models.user import Role, User, UserRole, UserToken
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """用户数据访问"""

    model = User

    async def get_by_id(self, user_id: int) -> Optional[User]:
        """
        按 ID 查询用户，并预加载角色。

        为什么要 selectinload(roles)？
        因为 has_role / is_admin 会读 user.roles，
        如果不预加载，访问时会触发惰性加载 —— 在异步环境里会直接报错。
        """
        stmt = (
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == user_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        """按用户名查询（登录时用）"""
        stmt = (
            select(User)
            .options(selectinload(User.roles))
            .where(User.username == username)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[User]:
        stmt = select(User).where(User.phone == phone)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_username(self, username: str, exclude_id: Optional[int] = None) -> bool:
        """
        用户名是否已存在。

        exclude_id 用于「更新资料时排除自己」——
        否则用户不改用户名只改邮箱，也会被判定为"用户名已存在"。
        """
        stmt = select(func.count()).select_from(User).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return (await self.db.execute(stmt)).scalar() > 0

    async def exists_email(self, email: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(func.count()).select_from(User).where(User.email == email)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return (await self.db.execute(stmt)).scalar() > 0

    async def exists_phone(self, phone: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(func.count()).select_from(User).where(User.phone == phone)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return (await self.db.execute(stmt)).scalar() > 0

    async def list_users(
        self,
        offset: int,
        limit: int,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        role_code: Optional[str] = None,
    ) -> tuple[Sequence[dict], int]:
        """
        管理员查询用户列表（带收藏数统计）。

        返回的是 dict 列表而不是 ORM 对象 —— 因为要带一个聚合出来的
        collection_count 字段，ORM 对象上没有这个属性。
        这种做法叫「DTO 投影」，比给模型加一个用不到的属性更干净。

        :return: (用户数据列表, 总数)
        """
        # 收藏数子查询：每个用户收藏了多少设备
        collection_count_sq = (
            select(
                EquipmentCollection.user_id.label("user_id"),
                func.count(EquipmentCollection.id).label("collection_count"),
            )
            .group_by(EquipmentCollection.user_id)
            .subquery()
        )

        conditions = []
        if keyword:
            # 关键字同时匹配用户名 / 姓名 / 邮箱 / 手机号
            like = f"%{keyword}%"
            conditions.append(
                or_(
                    User.username.like(like),
                    User.real_name.like(like),
                    User.email.like(like),
                    User.phone.like(like),
                )
            )
        if status:
            conditions.append(User.status == status)

        # 按角色筛选需要 join 中间表
        if role_code:
            role_user_ids = (
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == role_code)
                .scalar_subquery()
            )
            conditions.append(User.id.in_(role_user_ids))

        # 总数
        count_stmt = select(func.count()).select_from(User)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        # 数据
        stmt = (
            select(
                User.id,
                User.username,
                User.real_name,
                User.email,
                User.phone,
                User.status,
                User.created_at,
                func.coalesce(collection_count_sq.c.collection_count, 0).label("collection_count"),
            )
            .outerjoin(collection_count_sq, collection_count_sq.c.user_id == User.id)
            .order_by(User.id.desc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            stmt = stmt.where(*conditions)

        rows = (await self.db.execute(stmt)).mappings().all()
        users = [dict(row) for row in rows]

        # 单独把角色查出来（避免和聚合 join 混在一起导致结果集膨胀）
        if users:
            user_ids = [u["id"] for u in users]
            role_rows = (
                await self.db.execute(
                    select(UserRole.user_id, Role.code, Role.name)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(UserRole.user_id.in_(user_ids))
                )
            ).all()
            role_map: dict[int, list[dict]] = {}
            for uid, code, name in role_rows:
                role_map.setdefault(uid, []).append({"code": code, "name": name})
            for u in users:
                u["roles"] = role_map.get(u["id"], [])

        return users, total

    # ---------- 角色 ----------

    async def get_role_by_code(self, code: str) -> Optional[Role]:
        stmt = select(Role).where(Role.code == code)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def assign_role(self, user_id: int, role_id: int) -> None:
        """
        给用户分配角色。

        为什么用 INSERT IGNORE 语义而不是先查再插？
        因为复合主键 (user_id, role_id) 已经保证了不会重复，
        直接尝试插入、冲突就忽略，比"先查一次再插"少一次查询。
        MySQL 用 INSERT IGNORE，但这里为了数据库可移植性，
        用「先删同角色再插」的方式保证幂等。
        """
        exists = await self.db.execute(
            select(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == role_id
            )
        )
        if exists.scalar_one_or_none() is None:
            self.db.add(UserRole(user_id=user_id, role_id=role_id))
            await self.db.flush()

    async def replace_role(self, user_id: int, role_id: int) -> int:
        """
        把用户的角色替换为指定角色（用于授权/撤销管理员）。

        :return: 受影响的行数
        """
        stmt = update(UserRole).where(UserRole.user_id == user_id).values(role_id=role_id)
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def get_user_role_codes(self, user_id: int) -> List[str]:
        stmt = (
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ---------- 统计 ----------

    async def count_active_users(self) -> int:
        """统计启用状态的用户数"""
        stmt = select(func.count()).select_from(User).where(User.status == "active")
        return (await self.db.execute(stmt)).scalar() or 0


class UserTokenRepository(BaseRepository[UserToken]):
    """Token 数据访问"""

    model = UserToken

    async def get_by_token(self, token: str) -> Optional[UserToken]:
        """按 Token 值查询。token 字段有唯一索引，走索引很快。"""
        stmt = select(UserToken).where(UserToken.token == token)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: int) -> Optional[UserToken]:
        """
        按用户查 Token。

        因为有唯一约束 uq_user_token_user_id，一个用户最多只有一条，
        所以可以安全地用 scalar_one_or_none。
        """
        stmt = select(UserToken).where(UserToken.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_token(
        self,
        user_id: int,
        token: str,
        expires_at,
    ) -> UserToken:
        """
        创建或更新用户的 Token。

        语义：一个用户同时只有一个有效 Token，重新登录会顶掉旧的。
        这也是「换设备登录会把旧设备踢下线」的实现方式。
        """
        existing = await self.get_by_user_id(user_id)
        if existing is not None:
            existing.token = token
            existing.expires_at = expires_at
            existing.is_revoked = False
            await self.db.flush()
            return existing

        obj = UserToken(
            user_id=user_id,
            token=token,
            expires_at=expires_at,
            is_revoked=False,
        )
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def revoke_by_user_id(self, user_id: int) -> int:
        """撤销某用户的全部 Token（改密码、被禁用、登出时调用）"""
        stmt = (
            update(UserToken)
            .where(UserToken.user_id == user_id, UserToken.is_revoked == False)  # noqa: E712
            .values(is_revoked=True)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def delete_expired(self) -> int:
        """
        清理已过期的 Token 记录。

        由定时任务周期调用。为什么要清理？
        因为 Token 表会随着用户登录不断增长，过期数据留着只占空间、
        还会拖慢统计查询。过期即删，保持表轻量。
        """
        from app.core.security import utc_now

        stmt = delete(UserToken).where(UserToken.expires_at < utc_now())
        result = await self.db.execute(stmt)
        await self.db.flush()
        return result.rowcount

    async def count_active_tokens(self) -> int:
        """
        统计有效 Token 数。

        注意：这里是「当前有效的登录会话数」，不是"近30天活跃用户数"。
        想要后者需要基于 last_used_at 做时间范围过滤 ——
        这是一个容易混淆的概念差异，原项目就把两者混为一谈了
        （用 expires_at >= 30天前 来算"活跃用户"，语义其实是"Token 还没过期"）。
        """
        from app.core.security import utc_now

        stmt = (
            select(func.count())
            .select_from(UserToken)
            .where(UserToken.expires_at > utc_now(), UserToken.is_revoked == False)  # noqa: E712
        )
        return (await self.db.execute(stmt)).scalar() or 0

    async def count_recently_active_users(self, days: int = 30) -> int:
        """
        统计最近 N 天内有访问行为的用户数（真正的"活跃用户"）。

        依赖 last_used_at 字段 —— 每次请求认证成功时会更新它。
        """
        from datetime import timedelta

        from app.core.security import utc_now

        since = utc_now() - timedelta(days=days)
        stmt = (
            select(func.count(func.distinct(UserToken.user_id)))
            .where(UserToken.last_used_at >= since)
        )
        return (await self.db.execute(stmt)).scalar() or 0
