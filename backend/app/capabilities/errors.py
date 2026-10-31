"""能力层的错误类型。

放在 `app/capabilities/` 根下而不是某一层里，是为了让**注册层和执行层都能用**：
内置能力的实现要抛错（它属于注册层），执行层要把错误转成结构化结果，
如果错误类型住在执行层，注册层就得反过来依赖执行层——方向就错了。

错误必须分级：模型要能据此判断"改参数重试"还是"换条路"，所以每个错误码都带
``retryable``。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """执行失败的错误码。"""

    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    TIMEOUT = "TIMEOUT"
    SANDBOX_TIMEOUT = "TIMEOUT"  # 旧名保留
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RISK_BLOCKED = "RISK_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    CANCELLED = "CANCELLED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    TASK_BLOCKED = "TASK_BLOCKED"
    UNKNOWN = "UNKNOWN"


# 哪些错误让模型自己重试有希望
_RETRYABLE: dict[str, bool] = {
    ErrorCode.INVALID_ARGUMENTS: True,
    ErrorCode.SCHEMA_INVALID: True,
    ErrorCode.TIMEOUT: True,
    ErrorCode.NETWORK_ERROR: True,
    ErrorCode.HTTP_ERROR: True,
    ErrorCode.QUOTA_EXCEEDED: True,
}


class CapabilityError(Exception):
    """一次能力调用失败的原因。"""

    def __init__(
        self,
        message: str,
        code: ErrorCode | str = ErrorCode.EXECUTION_ERROR,
        retryable: bool | None = None,
        **details: Any,
    ) -> None:
        self.code = ErrorCode(code) if not isinstance(code, ErrorCode) else code
        self.message = message
        self.retryable = _RETRYABLE.get(self.code, False) if retryable is None else retryable
        self.details = details
        super().__init__(message)

    @classmethod
    def of(
        cls,
        code: ErrorCode | str,
        message: str,
        retryable: bool | None = None,
        **details: Any,
    ) -> CapabilityError:
        return cls(message, code=code, retryable=retryable, **details)

    def to_text(self) -> str:
        """渲染成给模型看的文本。"""
        hint = "（可以修正参数后重试）" if self.retryable else "（换个思路，不要重复同样的调用）"
        return f"[{self.code}] {self.message} {hint}"


# 兼容旧名
ExecutionError = CapabilityError

__all__ = ["CapabilityError", "ErrorCode", "ExecutionError"]
