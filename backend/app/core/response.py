"""
统一响应体与业务错误码。

设计要点（面试可以讲）：
1. 所有接口统一返回 {code, message, data} 三段式。
   这样前端只需要写一份响应处理逻辑，不用记"这个接口返回 detail、
   那个接口返回 message"——这正是原项目最大的接口一致性问题。

2. 区分「HTTP 状态码」和「业务码」：
   - HTTP 状态码表达传输层语义（200 成功 / 401 未认证 / 403 无权限 / 500 服务端错误）
   - 业务码表达具体业务原因（1001 用户不存在 / 2001 时间段冲突）
   两者都要，但职责不同。只靠 HTTP 状态码无法区分"用户不存在"和"密码错误"。

3. 用泛型 Response[T]，让 Swagger 文档能显示出 data 的具体结构。
"""
from enum import IntEnum
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorCode(IntEnum):
    """
    业务错误码。

    分段规则：
      0        成功
      1000-1999 通用/参数/认证授权
      2000-2999 用户模块
      3000-3999 设备模块
      4000-4999 预约模块
      5000-5999 文件上传模块
    """

    SUCCESS = 0

    # ---------- 通用（1000-1999） ----------
    PARAM_ERROR = 1000            # 请求参数校验失败
    UNAUTHORIZED = 1001           # 未登录 / Token 无效
    TOKEN_EXPIRED = 1002          # Token 已过期
    FORBIDDEN = 1003              # 已登录但权限不足
    NOT_FOUND = 1004              # 资源不存在
    METHOD_NOT_ALLOWED = 1005     # 请求方法不支持
    CONFLICT = 1006               # 资源冲突（如唯一约束）
    RATE_LIMITED = 1007           # 请求过于频繁
    INTERNAL_ERROR = 1500         # 服务器内部错误

    # ---------- 用户模块（2000-2999） ----------
    USER_NOT_FOUND = 2000
    USERNAME_EXISTS = 2001
    EMAIL_EXISTS = 2002
    PHONE_EXISTS = 2003
    PASSWORD_ERROR = 2004
    USER_DISABLED = 2005
    OLD_PASSWORD_ERROR = 2006
    CANNOT_OPERATE_SELF = 2007    # 不能对自己执行该操作（如删除自己）

    # ---------- 设备模块（3000-3999） ----------
    EQUIPMENT_NOT_FOUND = 3000
    EQUIPMENT_NOT_AVAILABLE = 3001
    SERIAL_NUMBER_EXISTS = 3002
    CATEGORY_NOT_FOUND = 3003
    LAB_NOT_FOUND = 3004
    COLLECTION_EXISTS = 3005      # 已收藏
    COLLECTION_NOT_FOUND = 3006   # 未收藏

    # ---------- 预约模块（4000-4999） ----------
    BOOKING_NOT_FOUND = 4000
    BOOKING_TIME_INVALID = 4001       # 结束时间早于开始时间
    BOOKING_DATE_INVALID = 4002       # 预约日期早于今天
    BOOKING_USER_CONFLICT = 4003      # 用户在该时间段已有其他预约
    BOOKING_EQUIPMENT_CONFLICT = 4004 # 设备在该时间段已被预约
    BOOKING_STATUS_INVALID = 4005     # 当前状态不允许该操作
    BOOKING_NOT_OWNER = 4006          # 不是自己的预约
    BOOKING_ALREADY_AUDITED = 4007    # 该预约已审核过

    # ---------- 文件上传（5000-5999） ----------
    FILE_EMPTY = 5000
    FILE_TOO_LARGE = 5001
    FILE_TYPE_NOT_ALLOWED = 5002
    FILE_SAVE_FAILED = 5003


# 业务码 -> 默认提示语
ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SUCCESS: "操作成功",
    ErrorCode.PARAM_ERROR: "请求参数有误",
    ErrorCode.UNAUTHORIZED: "请先登录",
    ErrorCode.TOKEN_EXPIRED: "登录已过期，请重新登录",
    ErrorCode.FORBIDDEN: "权限不足，无法执行该操作",
    ErrorCode.NOT_FOUND: "请求的资源不存在",
    ErrorCode.METHOD_NOT_ALLOWED: "请求方法不被支持",
    ErrorCode.CONFLICT: "数据冲突，请刷新后重试",
    ErrorCode.RATE_LIMITED: "操作过于频繁，请稍后再试",
    ErrorCode.INTERNAL_ERROR: "服务器开小差了，请稍后重试",
    ErrorCode.USER_NOT_FOUND: "用户不存在",
    ErrorCode.USERNAME_EXISTS: "该用户名已被注册",
    ErrorCode.EMAIL_EXISTS: "该邮箱已被注册",
    ErrorCode.PHONE_EXISTS: "该手机号已被注册",
    ErrorCode.PASSWORD_ERROR: "用户名或密码错误",
    ErrorCode.USER_DISABLED: "账号已被禁用，请联系管理员",
    ErrorCode.OLD_PASSWORD_ERROR: "原密码不正确",
    ErrorCode.CANNOT_OPERATE_SELF: "不能对自己执行该操作",
    ErrorCode.EQUIPMENT_NOT_FOUND: "设备不存在",
    ErrorCode.EQUIPMENT_NOT_AVAILABLE: "该设备当前不可预约",
    ErrorCode.SERIAL_NUMBER_EXISTS: "该设备序列号已存在",
    ErrorCode.CATEGORY_NOT_FOUND: "设备分类不存在",
    ErrorCode.LAB_NOT_FOUND: "实验室不存在",
    ErrorCode.COLLECTION_EXISTS: "该设备已在收藏列表中",
    ErrorCode.COLLECTION_NOT_FOUND: "收藏记录不存在",
    ErrorCode.BOOKING_NOT_FOUND: "预约记录不存在",
    ErrorCode.BOOKING_TIME_INVALID: "结束时间必须晚于开始时间",
    ErrorCode.BOOKING_DATE_INVALID: "预约日期不能早于今天",
    ErrorCode.BOOKING_USER_CONFLICT: "您在该时间段已有其他预约",
    ErrorCode.BOOKING_EQUIPMENT_CONFLICT: "该设备在所选时间段已被预约",
    ErrorCode.BOOKING_STATUS_INVALID: "当前预约状态不支持该操作",
    ErrorCode.BOOKING_NOT_OWNER: "只能操作自己的预约",
    ErrorCode.BOOKING_ALREADY_AUDITED: "该预约已经审核过了",
    ErrorCode.FILE_EMPTY: "上传的文件为空",
    ErrorCode.FILE_TOO_LARGE: "文件大小超出限制",
    ErrorCode.FILE_TYPE_NOT_ALLOWED: "不支持的文件类型",
    ErrorCode.FILE_SAVE_FAILED: "文件保存失败",
}


class Response(BaseModel, Generic[T]):
    """
    统一响应体。

    用法：
        return success(data=user_out)
        return success(message="注册成功", data=user_out)

    Swagger 里会正确显示 data 的结构，因为用了泛型。
    """

    code: int = Field(0, description="业务码，0 表示成功")
    message: str = Field("操作成功", description="提示信息")
    data: Optional[T] = Field(None, description="业务数据")


def success(data: Optional[T] = None, message: str = "操作成功") -> Response[T]:
    """构造成功响应"""
    return Response(code=ErrorCode.SUCCESS, message=message, data=data)


class PageData(BaseModel, Generic[T]):
    """分页数据的统一结构。

    原项目每个列表接口的分页字段名都不一样（有的是 total/page/page_size，
    有的是 total/hasMore），这里统一成一套，前端只写一次分页逻辑。

    注意一个 Python 陷阱：这里字段名叫 list，而 list 是内置类型名。
    在类体（class body）作用域内，一旦定义了 `list` 这个字段，
    后面再写 `list[T]` 就会被解析成那个 FieldInfo 对象而不是内置 list 类型，
    直接报 TypeError: 'FieldInfo' object is not subscriptable。
    规避方式有两种：
      1. 用 typing.List[T] 而不是内置 list[T]（本文件采用）
      2. 把 list 字段放到类定义的最后
    """
    # 先把带泛型的字段定义完，最后再定义名为 list 的字段，双保险
    total: int = Field(0, description="总记录数")
    page: int = Field(1, description="当前页码")
    page_size: int = Field(10, description="每页条数")
    total_pages: int = Field(0, description="总页数")
    has_more: bool = Field(False, description="是否还有下一页")
    list: List[T] = Field(default_factory=list, description="当前页数据")

    @classmethod
    def build(cls, items: List[T], total: int, page: int, page_size: int) -> "PageData[T]":
        """根据查询结果自动计算总页数和 has_more，避免每个接口各算一遍"""
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return cls(
            list=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_more=page < total_pages,
        )
