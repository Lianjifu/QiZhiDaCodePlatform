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
    """执行失败的错误码。

    分组看：前三组是"调用本身有问题"（找不到能力 / 参数不对 / 执行出错），
    接着是"治理不通过"（权限、风险、审批），最后是"平台状态"
    （配额、幂等冲突、熔断、重试用尽、任务阻塞）。
    """

    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"  # 能力不存在
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"  # 参数合法但不匹配目标（文件不存在等）
    SCHEMA_INVALID = "SCHEMA_INVALID"  # 参数不符合声明的 schema
    EXECUTION_ERROR = "EXECUTION_ERROR"  # 执行过程出错
    OUTPUT_INVALID = "OUTPUT_INVALID"  # 返回值不符合 output_schema
    TIMEOUT = "TIMEOUT"  # 执行超时
    SANDBOX_TIMEOUT = "TIMEOUT"  # 旧名保留（值与 TIMEOUT 相同）
    NETWORK_ERROR = "NETWORK_ERROR"  # 网络访问失败
    HTTP_ERROR = "HTTP_ERROR"  # HTTP 返回了错误状态码
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 权限判定不通过
    RISK_BLOCKED = "RISK_BLOCKED"  # 风险判定不通过
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"  # 需要人工审批（已挂起）
    APPROVAL_DENIED = "APPROVAL_DENIED"  # 人工拒绝
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"  # 审批超时过期
    CANCELLED = "CANCELLED"  # 被取消
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"  # 超出配额
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"  # 幂等键命中但参数不一致
    CIRCUIT_OPEN = "CIRCUIT_OPEN"  # 熔断打开，快速失败
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"  # 重试用尽
    TASK_BLOCKED = "TASK_BLOCKED"  # 任务已阻塞，转人工
    UNKNOWN = "UNKNOWN"  # 兜底


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
    """一次能力调用失败的原因。

    Args:
        message: 给人（和模型）看的说明。
        code: 错误码，决定归类到哪种失败分层。
        retryable: 是否值得重试；不传时按错误码的默认口径（见 ``_RETRYABLE``）。
        **details: 附加信息（如 HTTP 状态码），落在 ``details`` 里随结果一起返回。

    ``retryable`` 单独可传是有意的：默认口径是"这个错误码通常可重试"，
    但具体到某一次调用可能不可重试（比如超时发生在一个不幂等的能力上）。
    """

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
        """按"错误码优先"的顺序构造。"""
        return cls(message, code=code, retryable=retryable, **details)

    def to_text(self) -> str:
        """渲染成给模型看的文本。

        结尾一定带一句"该怎么做" —— 可重试的说"修正参数后重试"，
        不可重试的说"换个思路，别重复同样的调用"。
        """
        hint = "（可以修正参数后重试）" if self.retryable else "（换个思路，不要重复同样的调用）"
        return f"[{self.code}] {self.message} {hint}"


# 兼容旧名
ExecutionError = CapabilityError

__all__ = ["CapabilityError", "ErrorCode", "ExecutionError"]
