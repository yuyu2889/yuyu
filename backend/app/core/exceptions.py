"""
业务异常体系与全局异常处理器。

设计要点（面试可以讲）：
1. 业务代码里只 throw 业务异常（如 raise BusinessError(ErrorCode.USER_NOT_FOUND)），
   不需要在每个函数里关心"该返回什么 HTTP 状态码"。
   HTTP 状态码的映射集中在 HttpStatusMap 一张表里，改起来只改一处。

2. 全局注册处理器后，所有异常出口都被收拢，保证前端永远收到
   {code, message, data} 三段式响应，不会再出现 FastAPI 默认的
   {"detail": [...]} 这种另一种格式。

3. 敏感信息保护：DEBUG=false 时，响应里绝不返回 traceback 和原始 SQL 错误，
   只记进服务端日志。原项目把 traceback 直接返回给前端，是信息泄露。
"""
import logging
import traceback
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.response import ERROR_MESSAGES, ErrorCode, Response

logger = logging.getLogger(__name__)


# ====================== 异常类 ======================

class AppException(Exception):
    """
    所有业务异常的基类。

    属性：
        code:       业务错误码
        message:    给用户看的提示（可覆盖默认值）
        http_status:HTTP 状态码
        data:       附加信息（一般用于开发调试）
    """

    def __init__(
        self,
        code: ErrorCode,
        message: Optional[str] = None,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "请求失败")
        self.http_status = http_status
        self.data = data
        super().__init__(self.message)


class BusinessError(AppException):
    """业务规则不满足。默认 400。"""

    def __init__(self, code: ErrorCode, message: Optional[str] = None, data: Any = None) -> None:
        super().__init__(code, message, status.HTTP_400_BAD_REQUEST, data)


class AuthError(AppException):
    """未认证 / Token 无效 / 已过期。默认 401，并带上 WWW-Authenticate 头。"""

    def __init__(self, code: ErrorCode = ErrorCode.UNAUTHORIZED, message: Optional[str] = None) -> None:
        super().__init__(code, message, status.HTTP_401_UNAUTHORIZED)


class PermissionError_(AppException):
    """已登录但权限不足。默认 403。"""

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(ErrorCode.FORBIDDEN, message, status.HTTP_403_FORBIDDEN)


class NotFoundError(AppException):
    """资源不存在。默认 404。"""

    def __init__(self, code: ErrorCode = ErrorCode.NOT_FOUND, message: Optional[str] = None) -> None:
        super().__init__(code, message, status.HTTP_404_NOT_FOUND)


class ConflictError(AppException):
    """资源冲突，如唯一约束、时间段占用。默认 409。"""

    def __init__(self, code: ErrorCode = ErrorCode.CONFLICT, message: Optional[str] = None) -> None:
        super().__init__(code, message, status.HTTP_409_CONFLICT)


class FileUploadError(AppException):
    """文件上传相关错误。默认 400。"""

    def __init__(self, code: ErrorCode, message: Optional[str] = None) -> None:
        super().__init__(code, message, status.HTTP_400_BAD_REQUEST)


# ====================== 统一响应构造 ======================

def _error_response(
    code: ErrorCode,
    message: str,
    http_status: int,
    data: Any = None,
    headers: Optional[dict] = None,
) -> JSONResponse:
    """构造统一的错误响应。所有处理器最终都走这里，保证格式一致。"""
    payload = Response(code=int(code), message=message, data=data)
    return JSONResponse(
        status_code=http_status,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def _debug_payload(exc_type: str, detail: str, path: str) -> dict:
    """
    开发环境下的调试信息。

    生产环境（DEBUG=false）永远返回 None —— 不把堆栈和 SQL 详情泄露给前端。
    """
    if not settings.debug:
        return None
    return {"error_type": exc_type, "error_detail": detail, "path": path}


# ====================== 各异常处理器 ======================

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """处理我们自己抛出的业务异常"""
    logger.warning(
        "业务异常 | code=%s | message=%s | path=%s",
        exc.code.name, exc.message, request.url.path,
    )
    headers = None
    if exc.http_status == status.HTTP_401_UNAUTHORIZED:
        headers = {"WWW-Authenticate": "Bearer"}
    return _error_response(exc.code, exc.message, exc.http_status, exc.data, headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    处理 Pydantic 请求参数校验失败。

    关键点：FastAPI 默认返回 {"detail": [...]}，和我们的三段式格式不一致。
    这里把它转换成统一格式，并把每个字段的错误整理成人话，
    前端可以直接把 message 弹出来给用户看。
    """
    errors = []
    for err in exc.errors():
        # loc 形如 ("body", "username")，去掉第一段（来源），拼成 "username"
        field = " -> ".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        errors.append(f"{field or '参数'}: {err.get('msg', '格式不正确')}")

    message = "；".join(errors) if errors else ERROR_MESSAGES[ErrorCode.PARAM_ERROR]
    logger.warning("参数校验失败 | path=%s | %s", request.url.path, message)

    return _error_response(
        ErrorCode.PARAM_ERROR,
        message,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        _debug_payload("RequestValidationError", message, str(request.url)),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    处理 HTTPException（包括 FastAPI 内部抛的 404、405 等）。

    我们把 HTTP 状态码反向映射成业务码，保持格式统一。
    """
    code_map = {
        status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN: ErrorCode.FORBIDDEN,
        status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
        status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
        status.HTTP_429_TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
    }
    code = code_map.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    # exc.detail 可能是 FastAPI 给的英文默认值，优先用我们自己的中文提示
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else ERROR_MESSAGES.get(code, "请求失败")

    return _error_response(code, message, exc.status_code)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    处理数据库完整性约束错误。

    这类错误说明"应用层的校验漏了，被数据库兜住了"，属于应当修复的 bug，
    所以要记 error 级日志（方便排查），但返回给用户的是可读提示。
    """
    raw = str(getattr(exc, "orig", exc))
    logger.error("数据库约束冲突 | path=%s | %s", request.url.path, raw, exc_info=True)

    # 按常见约束名给出精准提示
    if "uq_user_username" in raw or "username" in raw:
        code, message = ErrorCode.USERNAME_EXISTS, ERROR_MESSAGES[ErrorCode.USERNAME_EXISTS]
        http_status = status.HTTP_409_CONFLICT
    elif "uq_user_email" in raw or "email" in raw:
        code, message = ErrorCode.EMAIL_EXISTS, ERROR_MESSAGES[ErrorCode.EMAIL_EXISTS]
        http_status = status.HTTP_409_CONFLICT
    elif "uq_user_phone" in raw or "phone" in raw:
        code, message = ErrorCode.PHONE_EXISTS, ERROR_MESSAGES[ErrorCode.PHONE_EXISTS]
        http_status = status.HTTP_409_CONFLICT
    elif "uq_booking" in raw or "FOREIGN KEY" in raw:
        code, message = ErrorCode.BOOKING_EQUIPMENT_CONFLICT, "该时间段已被预约，请选择其他时间"
        http_status = status.HTTP_409_CONFLICT
    else:
        code, message = ErrorCode.CONFLICT, ERROR_MESSAGES[ErrorCode.CONFLICT]
        http_status = status.HTTP_409_CONFLICT

    return _error_response(
        code, message, http_status,
        _debug_payload("IntegrityError", raw, str(request.url)),
    )


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """处理其他数据库错误（连接失败、SQL 语法错误等）"""
    logger.error("数据库操作异常 | path=%s | %s", request.url.path, exc, exc_info=True)
    return _error_response(
        ErrorCode.INTERNAL_ERROR,
        ERROR_MESSAGES[ErrorCode.INTERNAL_ERROR],
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        _debug_payload(type(exc).__name__, str(exc), str(request.url)),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    兜底处理器：捕获所有未被上面处理的异常。

    为什么必须打印完整堆栈？
    因为这类异常都代表代码 bug，只有日志里能看到真正原因。
    而返回给用户的只有一句"服务器开小差了"，避免泄露内部实现。
    """
    logger.error(
        "未处理异常 | path=%s | %s: %s",
        request.url.path, type(exc).__name__, exc,
        exc_info=True,
    )
    if settings.debug:
        # 开发环境把堆栈也放进 data，方便前端/Postman 直接看到原因
        data = _debug_payload(type(exc).__name__, str(exc), str(request.url))
        if data is not None:
            data["traceback"] = traceback.format_exc().splitlines()[-15:]
    else:
        data = None

    return _error_response(
        ErrorCode.INTERNAL_ERROR,
        ERROR_MESSAGES[ErrorCode.INTERNAL_ERROR],
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        data,
    )


# ====================== 注册入口 ======================

def register_exception_handlers(app: FastAPI) -> None:
    """
    注册全局异常处理器。

    注册顺序很重要：越具体的异常要越早注册。
    Starlette 会按 MRO（继承链）匹配，所以 AppException 的各子类
    注册同一个处理器即可，不用为每个子类写一遍。
    """
    app.add_exception_handler(AppException, app_exception_handler)              # 我们的业务异常
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # 参数校验
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)   # HTTP 异常
    app.add_exception_handler(IntegrityError, integrity_error_handler)          # 约束冲突
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)        # 其他数据库异常
    app.add_exception_handler(Exception, unhandled_exception_handler)           # 兜底
