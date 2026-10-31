"""执行结果。

执行层不只返回字符串，而是返回结构化结果：

- **给模型看的**：渲染后的文本（``to_text()``），保持可读性；
- **给人/系统看的**：结构化字段（``data``），用于轨迹、前端展示、后续自动化。

失败必须分级 —— 模型要能据此判断"改参数重试"还是"换条路"，所以每个错误码都带
``retryable``（见 `app/capabilities/errors.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode

_MAX_TEXT = 20000


@dataclass
class ExecutionResult:
    """一次执行的结果。"""

    ok: bool = True
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    artifacts: list[str] = field(default_factory=list)
    error: CapabilityError | None = None
    duration_ms: int = 0
    # 本次执行对应的调用记录 ID（写回阶段产生）
    invocation_id: int | None = None
    # 各阶段结论，写进调用记录便于定位"卡在哪一关"
    stages: list[dict[str, Any]] = field(default_factory=list)
    # 任务置信度与阻塞状态（由调用记录算出来）
    confidence: float | None = None
    blocked: bool = False

    @classmethod
    def failed(cls, error: CapabilityError, duration_ms: int = 0) -> ExecutionResult:
        return cls(ok=False, content=error.message, error=error, duration_ms=duration_ms)

    def to_text(self) -> str:
        """渲染成给模型看的文本。"""
        if self.ok:
            return _truncate(self.content)
        if self.error is None:
            return "执行失败"
        return _truncate(self.error.to_text())

    def to_dict(self) -> dict[str, Any]:
        """结构化视图（落库前需脱敏）。"""
        return {
            "ok": self.ok,
            "content": self.content,
            "data": self.data,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "artifacts": list(self.artifacts),
            "error_code": self.error.code.value if self.error else None,
            "error_message": self.error.message if self.error else None,
            "duration_ms": self.duration_ms,
            "confidence": self.confidence,
            "blocked": self.blocked,
        }


def _truncate(text: str) -> str:
    if len(text) <= _MAX_TEXT:
        return text
    return text[:_MAX_TEXT] + f"... (输出过长，已截断，共 {len(text)} 字符)"


__all__ = ["ErrorCode", "ExecutionResult"]
