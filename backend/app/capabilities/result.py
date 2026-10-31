"""执行结果与失败分级。

执行层不只返回字符串，而是返回结构化结果：

- **给模型看的**：渲染后的文本（``to_text()``），保持与之前一致的可读性；
- **给人/系统看的**：结构化字段，用于轨迹、前端展示、后续自动化。

失败必须分级 —— 模型要能据此判断"改参数重试"还是"换条路"，
所以每个错误码都带 `retryable`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

_MAX_TEXT = 20000


class ErrorCode(StrEnum):
    """执行失败的错误码。"""

    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNKNOWN = "UNKNOWN"


# 哪些错误让模型自己重试有希望
_RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.INVALID_ARGUMENTS: True,
    ErrorCode.SANDBOX_TIMEOUT: True,
    ErrorCode.NETWORK_ERROR: True,
}


@dataclass
class ExecutionError:
    """一次执行失败的原因。"""

    code: ErrorCode = ErrorCode.UNKNOWN
    message: str = ""
    retryable: bool = False
    details: dict = field(default_factory=dict)

    @classmethod
    def of(cls, code: ErrorCode, message: str, **details) -> ExecutionError:
        return cls(
            code=code,
            message=message,
            retryable=_RETRYABLE.get(code, False),
            details=details,
        )


@dataclass
class ExecutionResult:
    """一次执行的结果。"""

    ok: bool = True
    content: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    artifacts: list[str] = field(default_factory=list)
    error: ExecutionError | None = None
    duration_ms: int = 0

    @classmethod
    def failed(cls, error: ExecutionError, duration_ms: int = 0) -> ExecutionResult:
        return cls(ok=False, content=error.message, error=error, duration_ms=duration_ms)

    def to_text(self) -> str:
        """渲染成给模型看的文本。"""
        if self.ok:
            return _truncate(self.content)
        error = self.error
        if error is None:
            return "执行失败"
        hint = "（可以修正参数后重试）" if error.retryable else "（换个思路，不要重复同样的调用）"
        return _truncate(f"[{error.code}] {error.message} {hint}")


def _truncate(text: str) -> str:
    if len(text) <= _MAX_TEXT:
        return text
    return text[:_MAX_TEXT] + f"... (输出过长，已截断，共 {len(text)} 字符)"
