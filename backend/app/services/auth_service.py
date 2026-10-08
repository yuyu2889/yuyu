"""
认证服务：注册、登录、登出、Token 校验与续期、修改密码。

设计要点（面试可以讲）：
1. **事务边界在 service 层**。
   repository 只 flush，由 service 决定何时 commit。
   这样一个业务操作（比如注册 = 建用户 + 分配角色 + 签发 Token）
   要么全部成功，要么全部回滚，不会出现"用户建好了但没角色"的中间状态。

2. **登录接口的限流是必须的**。
   不限流就可以被暴力破解密码。V2 用令牌桶按「IP + 用户名」维度限流。

3. **登录失败不区分"用户不存在"和"密码错误"**。
   都返回同一句"用户名或密码错误"。为什么？
   如果区分，攻击者就能用这个接口枚举出系统里存在哪些用户名
   （这叫「用户名枚举漏洞」）。这是安全设计的基本要求。

4. **改密码后必须让所有旧 Token 失效**。
   否则：密码泄露后用户改了密码，但攻击者手里的 Token 还能用 7 天。
   这是很多人会漏掉的点。原项目就没有这个逻辑。
"""
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RoleCode, UserStatus
from app.core.exceptions import AuthError, BusinessError, ConflictError, NotFoundError
from app.core.response import ErrorCode
from app.core.security import (
    calc_token_expires_at,
    generate_token,
    get_password_hash,
    needs_rehash,
    utc_now,
    validate_password_strength,
    verify_password,
)
from app.models.user import User
from app.repositories.user import UserRepository, UserTokenRepository
from app.schemas.user import (
    ChangePasswordRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserUpdateRequest,
)

logger = logging.getLogger(__name__)


@dataclass
class LoginResult:
    """登录结果（service 返回的中间对象，由 router 转成响应模型）"""

    user: User
    token: str
    expires_at: object


class AuthService:
    """认证业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.user_repo = UserRepository(db)
        self.token_repo = UserTokenRepository(db)

    # ====================== 注册 ======================

    async def register(self, data: UserRegisterRequest) -> LoginResult:
        """
        用户注册。

        流程：
          1. 检查用户名 / 邮箱 / 手机号是否已被占用
          2. 密码哈希
          3. 创建用户，分配默认角色（学生）
          4. 签发 Token（注册后直接登录，不用再登一次）
          5. 提交事务

        注意：三个唯一性检查是分开做的，为的是给出**精准的**错误提示。
        如果只依赖数据库的唯一约束，就只能返回一句笼统的"数据冲突"。
        """
        # ---------- 1. 唯一性检查 ----------
        if await self.user_repo.exists_username(data.username):
            raise ConflictError(ErrorCode.USERNAME_EXISTS)
        if await self.user_repo.exists_email(data.email):
            raise ConflictError(ErrorCode.EMAIL_EXISTS)
        if await self.user_repo.exists_phone(data.phone):
            raise ConflictError(ErrorCode.PHONE_EXISTS)

        # ---------- 2. 创建用户 ----------
        user = User(
            username=data.username,
            password=get_password_hash(data.password),
            real_name=data.real_name,
            email=str(data.email),
            phone=data.phone,
            status=UserStatus.ACTIVE.value,
        )
        self.db.add(user)
        await self.db.flush()  # 拿到自增 id

        # ---------- 3. 分配默认角色 ----------
        # 注册的用户默认是「学生」。管理员需要由现有管理员在后台授予。
        # 安全点：绝不能允许注册请求里带 role 字段，否则任何人都能注册成管理员。
        default_role = await self.user_repo.get_role_by_code(RoleCode.STUDENT.value)
        if default_role is None:
            # 这是部署问题，不是用户的问题，所以要记 error 日志
            logger.error("默认角色 %s 不存在，请检查数据库初始化数据", RoleCode.STUDENT.value)
            raise BusinessError(ErrorCode.INTERNAL_ERROR, "系统未初始化角色数据，请联系管理员")
        await self.user_repo.assign_role(user.id, default_role.id)

        # ---------- 4. 签发 Token ----------
        token, expires_at = await self._issue_token(user.id)

        await self.db.commit()

        # 重新加载角色关系（响应里要返回 roles）
        user = await self.user_repo.get_by_id(user.id)
        logger.info("新用户注册成功 | username=%s | id=%s", user.username, user.id)

        return LoginResult(user=user, token=token, expires_at=expires_at)

    # ====================== 登录 ======================

    async def login(self, data: UserLoginRequest, client_ip: str = "unknown") -> LoginResult:
        """
        用户登录。

        流程：
          1. 按用户名查用户
          2. 校验密码
          3. 校验账号状态（是否被禁用）
          4. 签发 Token（同时会让该用户之前的 Token 失效）
          5. 记录登录日志（IP + 时间，便于安全审计）
        """
        user = await self.user_repo.get_by_username(data.username)

        # 用户不存在 和 密码错误 返回同样的提示（防用户名枚举）
        if user is None or not verify_password(data.password, user.password):
            logger.warning(
                "登录失败 | username=%s | ip=%s | 原因=%s",
                data.username, client_ip,
                "用户不存在" if user is None else "密码错误",
            )
            raise AuthError(ErrorCode.PASSWORD_ERROR, "用户名或密码错误")

        if not user.is_active:
            logger.warning("登录被拒（账号禁用） | username=%s | ip=%s", user.username, client_ip)
            raise AuthError(ErrorCode.USER_DISABLED, "账号已被禁用，请联系管理员")

        # 密码哈希强度升级：如果数据库里的哈希是用旧配置生成的，
        # 趁用户这次登录（我们手上正好有明文密码）顺手升级
        if needs_rehash(user.password):
            user.password = get_password_hash(data.password)
            logger.info("已升级用户密码哈希强度 | username=%s", user.username)

        token, expires_at = await self._issue_token(user.id)
        await self.db.commit()

        # 重新加载以带上角色
        user = await self.user_repo.get_by_id(user.id)
        logger.info("登录成功 | username=%s | ip=%s", user.username, client_ip)

        return LoginResult(user=user, token=token, expires_at=expires_at)

    # ====================== Token 校验 ======================

    async def verify_token(self, token_value: str) -> Optional[User]:
        """
        校验 Token 并返回用户（带滑动续期）。

        注意：这个方法是给"非 HTTP 场景"（比如脚本、定时任务）用的。
        HTTP 请求的 Token 校验走 app/api/deps.py 的 get_current_user，
        那里的实现更完整（有一次性的活跃时间节流更新）。

        这个方法保留是为了：
        1. 业务逻辑可以脱离 FastAPI 依赖单独测试
        2. 将来加 gRPC/CLI 入口时可以复用
        """
        db_token = await self.token_repo.get_by_token(token_value)
        if db_token is None or db_token.is_revoked:
            return None

        if db_token.expires_at < utc_now():
            return None

        user = await self.user_repo.get_by_id(db_token.user_id)
        if user is None or not user.is_active:
            return None

        # 滑动续期：剩余时间不足总时长的 50% 时自动延长
        from app.core.security import should_renew_token

        need_commit = False
        if should_renew_token(db_token.expires_at):
            db_token.expires_at = calc_token_expires_at()
            need_commit = True

        # 更新活跃时间（同样做节流，理由见 deps.get_current_user）
        now = utc_now()
        if db_token.last_used_at is None or (
            now - db_token.last_used_at
        ).total_seconds() >= 60:
            db_token.last_used_at = now
            need_commit = True

        if need_commit:
            await self.db.commit()

        return user

    # ====================== 登出 ======================

    async def logout(self, user: User) -> None:
        """
        登出：撤销该用户的 Token。

        这里直接撤销该用户的所有 Token（因为一个用户同时只有一个 Token）。
        另一种做法是删除记录，但保留记录 + 标记撤销 更利于安全审计
        （能查到"这个 Token 在什么时候被主动废弃的"）。
        """
        affected = await self.token_repo.revoke_by_user_id(user.id)
        await self.db.commit()
        logger.info("用户登出 | username=%s | 撤销 Token 数=%d", user.username, affected)

    # ====================== 修改密码 ======================

    async def change_password(self, user: User, data: ChangePasswordRequest) -> None:
        """
        修改密码。

        关键点：**改完密码要撤销所有 Token**。
        否则密码泄露后用户改了密码，攻击者手里的旧 Token 依然有效。

        流程：
          1. 校验原密码
          2. 新密码不能与原密码相同（否则改了等于没改）
          3. 更新密码哈希
          4. 撤销全部 Token → 用户需要用新密码重新登录
        """
        if not verify_password(data.old_password, user.password):
            raise BusinessError(ErrorCode.OLD_PASSWORD_ERROR, "原密码不正确")

        if data.old_password == data.new_password:
            raise BusinessError(ErrorCode.PARAM_ERROR, "新密码不能与原密码相同")

        error = validate_password_strength(data.new_password)
        if error:
            raise BusinessError(ErrorCode.PARAM_ERROR, error)

        user.password = get_password_hash(data.new_password)
        await self.token_repo.revoke_by_user_id(user.id)
        await self.db.commit()

        logger.info("用户修改密码成功，已撤销全部登录凭证 | username=%s", user.username)

    # ====================== 内部方法 ======================

    async def _issue_token(self, user_id: int) -> tuple[str, object]:
        """
        签发 Token。

        语义：一个用户同时只有一个有效 Token。
        重新登录会覆盖旧 Token —— 也就是"新设备登录会顶掉旧设备"。
        如果你希望支持多设备同时在线，去掉 user_tokens 表上的
        uq_user_token_user_id 唯一约束，并改成每次插入新记录即可。
        """
        token_value = generate_token()
        expires_at = calc_token_expires_at()
        await self.token_repo.upsert_token(user_id, token_value, expires_at)
        return token_value, expires_at


class UserService:
    """用户资料相关业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.user_repo = UserRepository(db)

    # ====================== 查询 ======================

    async def get_profile(self, user: User) -> User:
        """获取个人信息（user 已由依赖注入提供，这里只是为了统一入口）"""
        return user

    async def get_by_id(self, user_id: int) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(ErrorCode.USER_NOT_FOUND)
        return user

    # ====================== 更新资料 ======================

    async def update_profile(self, user: User, data: UserUpdateRequest) -> User:
        """
        更新个人信息（部分更新）。

        用 exclude_unset=True 实现「只更新传了的字段」：
        - 前端只传 email，那 real_name 和 phone 保持不变
        - 而不是把没传的字段更新成 None（那样会把数据清空）

        这是 PUT vs PATCH 语义的经典问题：
        严格来说部分更新应该用 PATCH，但很多项目用 PUT 实现部分更新，
        此时必须靠 exclude_unset 来保证语义正确。
        """
        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        if not update_data:
            raise BusinessError(ErrorCode.PARAM_ERROR, "没有需要更新的内容")

        # 唯一性冲突检查（要排除自己，否则不改也会冲突）
        if "email" in update_data:
            if await self.user_repo.exists_email(str(update_data["email"]), exclude_id=user.id):
                raise ConflictError(ErrorCode.EMAIL_EXISTS)
            update_data["email"] = str(update_data["email"])

        if "phone" in update_data:
            if await self.user_repo.exists_phone(update_data["phone"], exclude_id=user.id):
                raise ConflictError(ErrorCode.PHONE_EXISTS)

        for field, value in update_data.items():
            setattr(user, field, value)

        await self.db.commit()
        await self.db.refresh(user)
        logger.info("用户更新资料 | username=%s | 字段=%s", user.username, list(update_data))
        return await self.user_repo.get_by_id(user.id)
