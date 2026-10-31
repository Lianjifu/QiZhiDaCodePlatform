"""统一异常定义与业务异常基类。

业务代码只抛 `AppError` 子类，由全局异常处理器翻译成标准响应体，
避免每个 Service 都去构造 HTTPException（那会把 HTTP 语义泄漏到业务层）。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常基类。"""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "请求错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppError):
    """资源不存在。"""

    status_code = 404
    code = "NOT_FOUND"
    message = "资源不存在"


class ConflictError(AppError):
    """资源冲突，例如唯一键重复。"""

    status_code = 409
    code = "CONFLICT"
    message = "资源已存在"


class ValidationError(AppError):
    """业务规则校验失败。"""

    status_code = 422
    code = "VALIDATION_ERROR"
    message = "参数校验失败"


class UnauthorizedError(AppError):
    """未认证。"""

    status_code = 401
    code = "UNAUTHORIZED"
    message = "未认证"


class ForbiddenError(AppError):
    """无权限。"""

    status_code = 403
    code = "FORBIDDEN"
    message = "无访问权限"
