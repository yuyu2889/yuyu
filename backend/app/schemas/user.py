"""
用户相关的请求/响应模型。

设计要点（面试可以讲）：
1. 请求模型和响应模型必须分开，哪怕字段看起来很相似。
   UserCreate 有 password，UserResponse 绝对不能有 ——
   分开定义从机制上防止密码哈希被返回给前端。

2. 自定义校验器（field_validator）比"在路由里写 if"更好：
   - 校验规则跟着数据模型走，复用性高（注册、改密码都能用）
   - 校验失败自动变成 422 统一格式响应，不用手写错误处理
   - 规则集中一处，不会出现"注册校验了、改密码忘了校验"

3. 手机号用正则校验，而不是只校验长度。
   这是很实际的一点：只校验长度的"校验"等于没校验。
"""
import re
from datetime import datetime
from typing import List, Optional

from pydantic import EmailStr, Field, field_validator

from app.core.security import validate_password_strength
from app.schemas.common import ORMModel

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
# 用户名：字母开头，允许字母数字下划线，4-20 位
USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,19}$")


# ====================== 请求模型 ======================

class UserRegisterRequest(ORMModel):
    """注册请求"""

    username: str = Field(..., min_length=4, max_length=20, description="用户名，字母开头，4-20 位")
    password: str = Field(..., min_length=8, max_length=72, description="密码，至少 8 位")
    real_name: str = Field(..., min_length=2, max_length=20, description="真实姓名")
    email: EmailStr = Field(..., description="邮箱")
    phone: str = Field(..., description="手机号")

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        if not USERNAME_PATTERN.match(v):
            raise ValueError("用户名需以字母开头，只能包含字母、数字、下划线，长度 4-20 位")
        return v

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        """
        密码强度校验。

        注意：这个校验必须在后端做。前端的校验只是提升体验，
        攻击者可以直接调接口绕过前端。
        """
        error = validate_password_strength(v)
        if error:
            raise ValueError(error)
        return v

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        if not PHONE_PATTERN.match(v):
            raise ValueError("请输入正确的 11 位手机号")
        return v

    @field_validator("real_name")
    @classmethod
    def check_real_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("真实姓名不能为空")
        return v


class UserLoginRequest(ORMModel):
    """
    登录请求（JSON 格式）。

    OAuth2 标准要求用 x-www-form-urlencoded + OAuth2PasswordRequestForm，
    但那样前端要手动拼表单数据。V2 同时支持两种：
    - 本类：JSON 提交，前端更自然
    - OAuth2PasswordRequestForm：兼容 Swagger 的 Authorize 按钮
    """

    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserUpdateRequest(ORMModel):
    """
    更新个人信息（所有字段可选，支持部分更新）。

    exclude_unset=True 配合这个模型，就能实现"只更新传了的字段"。
    """

    real_name: Optional[str] = Field(None, min_length=2, max_length=20, description="真实姓名")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, description="手机号")

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not PHONE_PATTERN.match(v):
            raise ValueError("请输入正确的 11 位手机号")
        return v


class ChangePasswordRequest(ORMModel):
    """修改密码请求"""

    old_password: str = Field(..., min_length=1, description="原密码")
    new_password: str = Field(..., min_length=8, max_length=72, description="新密码，至少 8 位")

    @field_validator("new_password")
    @classmethod
    def check_new_password(cls, v: str) -> str:
        error = validate_password_strength(v)
        if error:
            raise ValueError(error)
        return v


class AdminChangeUserStatusRequest(ORMModel):
    """管理员启用/禁用用户"""

    # 只允许这两个值，用 Literal 比在路由里 if 判断更早拦截非法输入
    status: str = Field(..., pattern="^(active|disabled)$", description="active=启用, disabled=禁用")
    reason: Optional[str] = Field(None, max_length=200, description="禁用原因（会记录到日志）")


# ====================== 响应模型 ======================

class RoleBrief(ORMModel):
    """角色简要信息"""

    code: str = Field(..., description="角色编码")
    name: str = Field(..., description="角色名称")


class UserResponse(ORMModel):
    """
    用户信息响应。

    ⚠️ 这里绝对不包含 password 字段 —— 这是响应模型存在的首要意义。
    """

    id: int
    username: str
    real_name: str
    email: str
    phone: str
    status: str = Field(..., description="账号状态：active/disabled")
    created_at: Optional[datetime] = None
    roles: List[RoleBrief] = Field(default_factory=list, description="角色列表")
    is_admin: bool = Field(False, description="是否管理员（由角色推导，方便前端使用）")

    @classmethod
    def from_user(cls, user) -> "UserResponse":
        """
        从 ORM User 对象构造响应。

        为什么要这个类方法？
        因为 is_admin 不是 User 表里的字段，而是由 roles 推导出来的。
        集中在这里计算，避免每个接口都写一遍
        （原项目就是因为没做这件事，导致 is_admin 恒为 false）。
        """
        return cls(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            email=user.email,
            phone=user.phone,
            status=user.status,
            created_at=user.created_at,
            roles=[RoleBrief(code=r.code, name=r.name) for r in user.roles],
            is_admin=user.is_admin,
        )


class LoginResponse(ORMModel):
    """
    登录/注册成功响应。

    这里返回的 token 是 UUID 字符串（不是 JWT），
    前端拿到后放在 Authorization: Bearer <token> 里。
    """

    token: str = Field(..., description="访问令牌")
    token_type: str = Field("Bearer", description="令牌类型")
    expires_at: datetime = Field(..., description="过期时间（UTC）")
    expires_in: int = Field(..., description="有效期（秒）")
    user: UserResponse = Field(..., description="用户信息")


class UserListItem(ORMModel):
    """管理员用户列表项"""

    id: int
    username: str
    real_name: str
    email: str
    phone: str
    status: str
    created_at: Optional[datetime] = None
    collection_count: int = Field(0, description="收藏设备数")
    roles: List[RoleBrief] = Field(default_factory=list)
    is_admin: bool = False
