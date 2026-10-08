"""
用户路由：个人资料、修改密码、管理员用户管理。

设计要点（面试可以讲）：
1. **权限声明化**：管理员接口用 `dependencies=[Depends(require_admin)]` 声明，
   而不是在函数体里写 if 判断。
   好处：
   - 一眼看出接口的访问级别（读代码时不用翻到函数内部）
   - 权限策略集中（要改规则只改 require_admin 的定义）
   - 便于自动化审计（可以扫一遍路由自动生成权限矩阵）

2. **管理的"自操作"限制**：
   管理员不能禁用自己、不能删除自己、不能撤销自己的管理员权限。
   为什么？防止管理员误操作把自己锁在外面，导致系统没人能管理。
   这类"防呆"设计在真实系统里非常重要。
"""
import logging

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    PageParamsDep,
    require_admin,
)
from app.core.enums import RoleCode
from app.core.exceptions import BusinessError, NotFoundError
from app.core.response import ErrorCode, PageData, Response, success
from app.repositories.user import UserRepository, UserTokenRepository
from app.schemas.common import MessageResponse
from app.schemas.user import (
    AdminChangeUserStatusRequest,
    ChangePasswordRequest,
    UserListItem,
    UserResponse,
    UserUpdateRequest,
)
from app.services.auth_service import AuthService, UserService

logger = logging.getLogger(__name__)

# 注意路由注册顺序：/me 这类固定路径要放在 /{user_id} 之前，
# 否则 FastAPI 会先把 "me" 当成 user_id 去匹配（然后报类型错误）。
router = APIRouter(prefix="/users", tags=["用户"])


# ====================== 个人资料（需登录） ======================

@router.get(
    "/me",
    response_model=Response[UserResponse],
    summary="获取个人信息",
)
async def get_my_profile(current_user: CurrentUser) -> Response[UserResponse]:
    return success(data=UserResponse.from_user(current_user))


@router.put(
    "/me",
    response_model=Response[UserResponse],
    summary="更新个人信息",
    description="支持部分更新：只传需要修改的字段即可，未传的字段保持原值。",
)
async def update_my_profile(
    data: UserUpdateRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[UserResponse]:
    service = UserService(db)
    user = await service.update_profile(current_user, data)
    return success(data=UserResponse.from_user(user), message="个人信息更新成功")


@router.put(
    "/me/password",
    response_model=Response[dict],
    summary="修改密码",
    description=(
        "修改成功后**所有登录凭证会被撤销**，需要用新密码重新登录。\n\n"
        "这是必要的安全措施：否则密码泄露后用户改了密码，攻击者手里的 Token 依然有效。"
    ),
)
async def change_my_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    service = AuthService(db)
    await service.change_password(current_user, data)
    return success(
        data={"username": current_user.username},
        message="密码修改成功，请使用新密码重新登录",
    )


# ====================== 管理员：用户管理 ======================

@router.get(
    "/",
    response_model=Response[PageData[UserListItem]],
    summary="用户列表（管理员）",
    description="支持按关键字（用户名/姓名/邮箱/手机号）和状态、角色筛选。",
    dependencies=[Depends(require_admin)],
)
async def admin_list_users(
    db: DbSession,
    page_params: PageParamsDep,
    keyword: str = Query(None, description="关键字：匹配用户名/姓名/邮箱/手机号"),
    status_filter: str = Query(None, alias="status", description="状态筛选：active/disabled"),
    role: str = Query(None, description="角色筛选：student/teacher/admin"),
) -> Response[PageData[UserListItem]]:
    """
    管理员查询用户列表。

    注意参数名用了 status_filter + alias="status"：
    因为 status 是 FastAPI 的常用导入名，直接用作参数名容易和
    `from fastapi import status` 冲突。用 alias 保持对外接口叫 status。
    """
    repo = UserRepository(db)
    rows, total = await repo.list_users(
        offset=page_params.offset,
        limit=page_params.page_size,
        keyword=keyword,
        status=status_filter,
        role_code=role,
    )

    # 把 dict 转成响应模型，并计算 is_admin
    items = [
        UserListItem(
            **row,
            is_admin=any(r["code"] == RoleCode.ADMIN.value for r in row.get("roles", [])),
        )
        for row in rows
    ]

    return success(
        data=PageData.build(items, total, page_params.page, page_params.page_size),
        message="查询用户列表成功",
    )


@router.put(
    "/{user_id}/status",
    response_model=Response[UserResponse],
    summary="启用/禁用用户（管理员）",
    description=(
        "禁用用户后，该用户**当前所有 Token 立即失效**，无法继续访问任何接口。\n\n"
        "这是 UUID Token 方案相对 JWT 的核心优势：撤销立即生效，"
        "而 JWT 必须等它自然过期（除非额外维护黑名单）。"
    ),
    dependencies=[Depends(require_admin)],
)
async def admin_change_user_status(
    user_id: int,
    data: AdminChangeUserStatusRequest,
    admin: AdminUser,
    db: DbSession,
) -> Response[UserResponse]:
    # 防呆：不能禁用自己的账号
    if admin.id == user_id:
        raise BusinessError(ErrorCode.CANNOT_OPERATE_SELF, "不能禁用自己的账号")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(ErrorCode.USER_NOT_FOUND)

    old_status = user.status
    user.status = data.status

    # 关键：禁用用户时立即撤销其所有 Token
    if data.status == "disabled":
        token_repo = UserTokenRepository(db)
        revoked = await token_repo.revoke_by_user_id(user_id)
        logger.warning(
            "管理员禁用用户 | admin=%s | target=%s | 撤销Token数=%d | 原因=%s",
            admin.username, user.username, revoked, data.reason or "未填写",
        )
    else:
        logger.info("管理员启用用户 | admin=%s | target=%s", admin.username, user.username)

    await db.commit()
    user = await repo.get_by_id(user_id)

    action = "启用" if data.status == "active" else "禁用"
    return success(
        data=UserResponse.from_user(user),
        message=f"已{action}用户「{user.real_name}」（原状态：{old_status}）",
    )


@router.put(
    "/{user_id}/role",
    response_model=Response[UserResponse],
    summary="授予/撤销管理员（管理员）",
    description="target_role=admin 授予管理员权限，target_role=student 降级为普通用户。",
    dependencies=[Depends(require_admin)],
)
async def admin_change_user_role(
    user_id: int,
    admin: AdminUser,
    db: DbSession,
    target_role: str = Query(..., pattern="^(admin|student|teacher)$", description="目标角色编码"),
) -> Response[UserResponse]:
    """
    变更用户角色。

    两条安全约束：
      1. 不能改自己的角色（防止误操作把自己降级，导致没人能管理）
      2. 不能撤销最后一个管理员（防止系统失去管理员）
    """
    if admin.id == user_id:
        raise BusinessError(ErrorCode.CANNOT_OPERATE_SELF, "不能修改自己的角色")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(ErrorCode.USER_NOT_FOUND)

    # 撤销管理员时，检查系统里是否还有别的管理员
    if target_role != RoleCode.ADMIN.value and user.is_admin:
        admin_role = await repo.get_role_by_code(RoleCode.ADMIN.value)
        if admin_role is not None:
            from sqlalchemy import func, select

            from app.models.user import UserRole

            admin_count = (
                await db.execute(
                    select(func.count())
                    .select_from(UserRole)
                    .where(UserRole.role_id == admin_role.id)
                )
            ).scalar() or 0
            if admin_count <= 1:
                raise BusinessError(
                    ErrorCode.CONFLICT,
                    "系统至少需要保留一名管理员，无法撤销最后一名管理员的权限",
                )

    target = await repo.get_role_by_code(target_role)
    if target is None:
        raise NotFoundError(ErrorCode.NOT_FOUND, f"角色 {target_role} 不存在")

    await repo.replace_role(user_id, target.id)
    await db.commit()

    user = await repo.get_by_id(user_id)
    action = "授予管理员权限" if target_role == RoleCode.ADMIN.value else f"变更为{target.name}"
    logger.warning(
        "管理员变更用户角色 | admin=%s | target=%s | 新角色=%s",
        admin.username, user.username, target_role,
    )
    return success(data=UserResponse.from_user(user), message=f"已{action}")


@router.delete(
    "/{user_id}",
    response_model=Response[MessageResponse],
    summary="删除用户（管理员）",
    description=(
        "删除用户会级联删除其所有预约和收藏记录（数据库外键 ON DELETE CASCADE）。\n\n"
        "**注意**：这是物理删除，不可恢复。生产环境建议改成软删除。"
    ),
    dependencies=[Depends(require_admin)],
)
async def admin_delete_user(
    user_id: int,
    admin: AdminUser,
    db: DbSession,
) -> Response[MessageResponse]:
    if admin.id == user_id:
        raise BusinessError(ErrorCode.CANNOT_OPERATE_SELF, "不能删除自己的账号")

    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(ErrorCode.USER_NOT_FOUND)

    username = user.username
    real_name = user.real_name

    await repo.delete(user)
    await db.commit()

    logger.warning("管理员删除用户 | admin=%s | target=%s", admin.username, username)
    return success(
        data=MessageResponse(message=f"用户「{real_name}」已删除"),
        message="用户删除成功",
    )
