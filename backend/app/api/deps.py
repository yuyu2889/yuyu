"""
FastAPI 依赖项（Dependency Injection）。

设计要点（面试可以讲，这一块很能体现工程水平）：

1. **为什么要把「获取当前用户」抽成依赖？**
   原项目在每个需要登录的接口上写 `current_user: User = Depends(get_current_user)`，
   这部分其实已经做了。但权限判断没有抽：
   ```python
   # 原项目：这段代码在 admin.py 里复制了 11 次
   is_admin = any(role.id == 2 for role in user.roles) if user.roles else False
   if not is_admin:
       raise HTTPException(status_code=403, detail="权限不足")
   ```
   复制 11 遍的后果：想改权限规则要改 11 个地方，漏一个就是安全漏洞。
   V2 抽成 `Depends(require_admin)`，一处定义、处处复用。

2. **工厂函数模式**：`require_roles(...)` 返回一个依赖函数。
   这样权限要求可以按接口声明，一眼就能看出"这个接口谁能访问"：
   ```python
   @router.delete("/{id}", dependencies=[Depends(require_admin)])
   ```
   比在函数体里写 if 判断更声明式，也更容易做自动化测试和权限审计。

3. **HTTPBearer 的 auto_error=False**：
   默认情况下，缺少 Authorization 头时 FastAPI 返回 403，
   但语义上"没有凭证"应该是 401（未认证），"凭证不够权限"才是 403。
   设成 False 后我们自己抛 401，语义正确，前端也好区分处理。

4. **滑动续期（sliding expiration）**：
   每次请求都检查 Token 剩余有效期，快过期就自动延长。
   这样活跃用户永远不会被踢下线，不活跃的用户自然过期。
   实现上要注意：不能每次请求都写库（那样每次请求都多一次 UPDATE），
   只在"确实需要续期"时才写。
"""
import logging
from typing import Annotated, Callable, List, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import RoleCode
from app.core.exceptions import AuthError, PermissionError_
from app.core.response import ErrorCode
from app.core.security import calc_token_expires_at, should_renew_token, utc_now
from app.models.user import User, UserToken
from app.repositories.user import UserRepository, UserTokenRepository
from app.schemas.common import PageParams

logger = logging.getLogger(__name__)

# auto_error=False：缺少请求头时不自动抛错，交给我们处理，保证返回 401 而非 403
bearer_scheme = HTTPBearer(auto_error=False, description="格式：Bearer <token>")

# 类型别名：让路由签名更简洁
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ====================== 仓库依赖 ======================

def get_user_repo(db: DbSession) -> UserRepository:
    """用户仓库依赖"""
    return UserRepository(db)


def get_token_repo(db: DbSession) -> UserTokenRepository:
    """Token 仓库依赖"""
    return UserTokenRepository(db)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repo)]
TokenRepoDep = Annotated[UserTokenRepository, Depends(get_token_repo)]


# ====================== 认证依赖 ======================

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    user_repo: UserRepoDep = None,
    token_repo: TokenRepoDep = None,
) -> User:
    """
    获取当前登录用户。

    完整流程：
      1. 从 Authorization: Bearer <token> 取出 token
      2. 查 user_tokens 表
      3. 校验：是否存在 → 是否被撤销 → 是否过期
      4. 查用户 → 校验账号是否被禁用
      5. 滑动续期（快过期才写库）
      6. 返回 User 对象（已预加载 roles）

    任何一步失败都抛 401，并且提示语尽量一致
    —— 不要告诉攻击者"是 token 不存在还是已过期"，避免信息泄露。
    """
    if credentials is None or not credentials.credentials:
        raise AuthError(ErrorCode.UNAUTHORIZED, "请先登录")

    token_value = credentials.credentials

    # ---------- 1. 查 Token ----------
    db_token = await token_repo.get_by_token(token_value)
    if db_token is None:
        raise AuthError(ErrorCode.UNAUTHORIZED, "登录凭证无效，请重新登录")

    # ---------- 2. 是否被撤销 ----------
    if db_token.is_revoked:
        raise AuthError(ErrorCode.UNAUTHORIZED, "登录凭证已失效，请重新登录")

    # ---------- 3. 是否过期 ----------
    if db_token.expires_at < utc_now():
        # 过期是明确的一种情况，单独给错误码，前端可以据此弹"登录已过期"
        raise AuthError(ErrorCode.TOKEN_EXPIRED, "登录已过期，请重新登录")

    # ---------- 4. 查用户 ----------
    user = await user_repo.get_by_id(db_token.user_id)
    if user is None:
        raise AuthError(ErrorCode.UNAUTHORIZED, "用户不存在")

    # 账号被禁用 → 立即失效（这正是选 UUID Token 而非 JWT 的原因：
    # 管理员禁用用户后能立刻生效，不需要等 Token 自然过期）
    if not user.is_active:
        raise AuthError(ErrorCode.USER_DISABLED, "账号已被禁用，请联系管理员")

    # ---------- 5. 滑动续期 + 活跃时间更新 ----------
    now = utc_now()
    need_commit = False

    # 5.1 滑动续期：只在"快过期"时才延长
    if should_renew_token(db_token.expires_at):
        old_expires = db_token.expires_at
        db_token.expires_at = calc_token_expires_at()
        need_commit = True
        logger.info(
            "Token 已自动续期 | user=%s | %s -> %s",
            user.username, old_expires, db_token.expires_at,
        )

    # 5.2 更新最后使用时间（用于"活跃用户"统计）
    #
    # ⚠️ 这里有两个坑，都踩过：
    #
    # 坑 1：只改内存不 commit，改动就会丢。
    #   第一版我只写了 `db_token.last_used_at = now`，没有 commit，
    #   而 get_db() 在请求结束时只 close 不提交，
    #   结果数据库里 last_used_at 一直是 NULL，
    #   统计接口的"近30天活跃用户"永远是 0。
    #
    # 坑 2：不能每个请求都 UPDATE。
    #   每次请求写一次数据库，在 QPS 高的时候是纯粹的浪费
    #   （登录态检查本身是读多写少的场景）。
    #   所以加了节流：同一分钟内只更新一次。
    #   代价是"活跃时间"的精度是分钟级，对统计完全够用。
    #   生产环境更彻底的做法是异步批量刷盘（把变更攒起来定期写）。
    ACTIVE_TIME_UPDATE_INTERVAL_SECONDS = 60

    if db_token.last_used_at is None or (
        now - db_token.last_used_at
    ).total_seconds() >= ACTIVE_TIME_UPDATE_INTERVAL_SECONDS:
        db_token.last_used_at = now
        need_commit = True

    if need_commit:
        await token_repo.commit()

    return user


# 类型别名：路由里直接写 current_user: CurrentUser 即可
CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    user_repo: UserRepoDep = None,
    token_repo: TokenRepoDep = None,
) -> Optional[User]:
    """
    可选登录：带了有效 Token 就返回用户，否则返回 None（不报错）。

    使用场景：设备详情接口 —— 未登录也能看，但登录了可以额外返回
    "你是否收藏了这台设备"。这类接口用可选认证。
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, user_repo, token_repo)
    except AuthError:
        # 可选认证场景下，Token 无效不应该阻断请求，静默当作未登录
        return None


OptionalUser = Annotated[Optional[User], Depends(get_current_user_optional)]


# ====================== 权限依赖（工厂模式） ======================

def require_roles(*role_codes: RoleCode) -> Callable:
    """
    生成一个「要求指定角色之一」的依赖函数。

    使用示例：
        # 只允许管理员
        @router.delete("/users/{id}", dependencies=[Depends(require_admin)])
        async def delete_user(...): ...

        # 管理员或教师都可以
        @router.post("/equipment", dependencies=[Depends(require_roles(RoleCode.ADMIN, RoleCode.TEACHER))])

    设计说明：用工厂函数返回依赖，而不是写死一个 require_admin。
    这样新增角色时不用改这个文件，只需要在使用处声明。
    """

    async def _checker(current_user: CurrentUser) -> User:
        allowed = {code.value for code in role_codes}
        user_roles = set(current_user.role_codes)

        if not (allowed & user_roles):
            # 提示里说清需要什么角色，方便排查（但不泄露系统内部结构）
            raise PermissionError_(
                f"权限不足，该操作需要以下角色之一：{'、'.join(allowed)}"
            )
        return current_user

    return _checker


# 常用权限依赖（预定义好，路由里直接引用）
require_admin = require_roles(RoleCode.ADMIN)
require_admin_or_teacher = require_roles(RoleCode.ADMIN, RoleCode.TEACHER)

# 类型别名
AdminUser = Annotated[User, Depends(require_admin)]


# ====================== 分页依赖 ======================

def get_page_params(
    page: int = 1,
    page_size: int = 10,
) -> PageParams:
    """
    分页参数依赖。

    注意：这里不用 Query(...) 声明，因为 PageParams 内部已经用 Field 定义了
    约束（ge=1, le=100），FastAPI 会自动应用。
    这样路由签名里只写 `page_params: PageParamsDep` 就够了，
    不用每个接口重复声明 page/page_size 的参数约束。
    """
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(get_page_params)]


# ====================== 客户端信息依赖 ======================

def get_client_info(request: Request) -> dict:
    """
    获取客户端信息（用于日志和限流）。

    安全说明：X-Forwarded-For 可以被客户端伪造。
    只有在「服务部署在可信反向代理之后」时才应该信任这个头。
    本项目开发环境没有代理，所以回落到 request.client.host。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"

    return {
        "ip": ip,
        "user_agent": request.headers.get("User-Agent", "")[:200],
    }


ClientInfoDep = Annotated[dict, Depends(get_client_info)]
