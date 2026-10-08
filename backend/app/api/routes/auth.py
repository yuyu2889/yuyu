"""
认证路由：注册、登录、登出。

设计要点（面试可以讲）：
1. **路由层只做三件事**：解析参数、调用 service、包装响应。
   不写任何业务判断（比如"密码对不对""状态是不是 pending"）——
   那是 service 的职责。这样做的价值是：换一个入口（比如加个 gRPC 接口
   或定时任务调用）时，业务逻辑可以完整复用。

2. **登录接口必须限流**。
   令牌桶按「IP + 用户名」维度限流：只按 IP 会被 NAT 后的多个用户互相影响，
   只按用户名则无法防住"换用户名暴力猜测"。两者结合最合理。

3. **同时提供 JSON 和 OAuth2 表单两种登录方式**：
   - JSON 版给前端用（更自然）
   - OAuth2 表单版给 Swagger 的 Authorize 按钮用（能直接在文档里调试）
"""
import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import ClientInfoDep, CurrentUser, DbSession
from app.core.config import settings
from app.core.rate_limit import get_client_ip, login_limiter, register_limiter
from app.core.response import ErrorCode, Response, success
from app.core.security import utc_now
from app.schemas.user import (
    LoginResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])


def _build_login_response(result) -> LoginResponse:
    """把 service 返回的 LoginResult 转成响应模型（路由层的职责）"""
    return LoginResponse(
        token=result.token,
        token_type="Bearer",
        expires_at=result.expires_at,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserResponse.from_user(result.user),
    )


@router.post(
    "/register",
    response_model=Response[LoginResponse],
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description=(
        "注册成功后直接返回 Token，无需再次登录。\n\n"
        "**密码要求**：至少 8 位，不能是纯数字或纯字母。"
    ),
)
async def register(
    data: UserRegisterRequest,
    request: Request,
    db: DbSession,
) -> Response[LoginResponse]:
    """
    用户注册。

    注意这里用的是 POST + 201 Created 状态码 —— 语义上"创建了一个资源"。
    原项目用的是 200，虽然能用，但语义不够准确。
    """
    # 注册限流：防止有人批量注册垃圾账号
    register_limiter.check(f"register:{get_client_ip(request)}")

    service = AuthService(db)
    result = await service.register(data)
    return success(data=_build_login_response(result), message="注册成功，欢迎使用")


@router.post(
    "/login",
    response_model=Response[LoginResponse],
    summary="用户登录（JSON）",
    description="前端使用的登录接口，请求体为 JSON。密码错误与用户不存在返回相同提示，防止用户名枚举。",
)
async def login(
    data: UserLoginRequest,
    request: Request,
    db: DbSession,
    client: ClientInfoDep,
) -> Response[LoginResponse]:
    # 按「IP + 用户名」双重维度限流
    login_limiter.check(f"login:{client['ip']}:{data.username}")

    service = AuthService(db)
    result = await service.login(data, client_ip=client["ip"])
    return success(data=_build_login_response(result), message="登录成功")


@router.post(
    "/login/oauth2",
    response_model=Response[LoginResponse],
    summary="用户登录（OAuth2 表单，供 Swagger 调试）",
    description="兼容 OAuth2 标准的表单登录。在 Swagger 页面点右上角 Authorize 按钮时使用这个接口。",
    include_in_schema=True,
)
async def login_oauth2(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: DbSession = None,
    client: ClientInfoDep = None,
) -> Response[LoginResponse]:
    login_limiter.check(f"login:{client['ip']}:{form_data.username}")

    service = AuthService(db)
    result = await service.login(
        UserLoginRequest(username=form_data.username, password=form_data.password),
        client_ip=client["ip"],
    )
    return success(data=_build_login_response(result), message="登录成功")


@router.post(
    "/logout",
    response_model=Response[dict],
    summary="退出登录",
    description="撤销当前用户的登录凭证。登出后原 Token 立即失效。",
)
async def logout(
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    """
    登出。

    注意：这里必须要求登录（CurrentUser 依赖）。
    虽然"撤销一个不存在的 Token"无害，但要求登录能让审计日志更准确。
    """
    service = AuthService(db)
    await service.logout(current_user)
    return success(data={"username": current_user.username}, message="已退出登录")


@router.get(
    "/me",
    response_model=Response[UserResponse],
    summary="获取当前登录用户",
    description="用于前端刷新页面后恢复登录态。返回用户的角色和 is_admin 标记。",
)
async def get_me(current_user: CurrentUser) -> Response[UserResponse]:
    return success(data=UserResponse.from_user(current_user))
