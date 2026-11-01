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
    """一次执行的结果。

    成功时 ``ok=True``、``content`` 是给模型看的文本；失败时 ``ok=False``、
    ``error`` 里带着错误码和消息。两者都有 ``data`` —— 能力返回的结构化字段。
    """

    ok: bool = True  # 这次调用是否成功
    content: str = ""  # 给模型看的文本（渲染好的）
    data: dict[str, Any] = field(default_factory=dict)  # 能力返回的结构化字段
    stdout: str = ""  # 标准输出（执行类能力）
    stderr: str = ""  # 标准错误（执行类能力）
    exit_code: int | None = None  # 退出码（执行类能力）
    artifacts: list[str] = field(default_factory=list)  # 产出物路径等
    error: CapabilityError | None = None  # 失败时的原因
    duration_ms: int = 0  # 耗时（毫秒）
    # 本次执行对应的调用记录 ID（写回阶段产生）
    invocation_id: int | None = None
    # 各阶段结论，写进调用记录便于定位"卡在哪一关"
    stages: list[dict[str, Any]] = field(default_factory=list)
    # 任务置信度与阻塞状态（由调用记录算出来）
    confidence: float | None = None  # 任务置信度（0~100）
    blocked: bool = False  # 任务是否已阻塞（要转人工）

    @classmethod
    def failed(cls, error: CapabilityError, duration_ms: int = 0) -> ExecutionResult:
        """构造一个失败结果。"""
        return cls(ok=False, content=error.message, error=error, duration_ms=duration_ms)

    def to_text(self) -> str:
        """渲染成给模型看的文本。

        失败时用 ``error.to_text()`` —— 里面已经带了"该重试还是换路"的指令，
        分层渲染在 middleware 里再叠加更具体的上下文。
        """
        if self.ok:
            return _truncate(self.content)
        if self.error is None:
            return "执行失败"
        return _truncate(self.error.to_text())

    def to_dict(self) -> dict[str, Any]:
        """结构化视图（落库前需脱敏）。

        注意这里**不含参数** —— 参数在调用记录的另一列里（那边要单独处理
        审批场景，见 ``execution/pipeline.py`` 的 ``_finish``）。
        """
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
    """截断过长的文本，并说明原始长度。"""
    if len(text) <= _MAX_TEXT:
        return text
    return text[:_MAX_TEXT] + f"... (输出过长，已截断，共 {len(text)} 字符)"


def execution_artifact(result: ExecutionResult) -> dict[str, Any]:
    """执行摘要：给程序看，**不进模型上下文**。

    放在 ``ToolMessage.artifact`` 里 —— LangChain 专门留的"不发给模型、但要
    带回给调用方"的字段。没有它的话，调用方只知道模型调了什么能力，
    不知道这次调用是成功了、失败了，还是卡在审批上。

    ``status`` 有三档：``succeeded`` / ``failed`` / ``pending``（挂起等审批）。
    """
    status = "succeeded"
    if result.error is not None and result.error.code == ErrorCode.APPROVAL_REQUIRED:
        # 挂起等审批不是失败 —— 调用方要据此显示"等待确认"，而不是"出错"
        status = "pending"
    elif not result.ok:
        status = "failed"
    return {
        "invocation_id": result.invocation_id,
        "ok": result.ok,
        "status": status,
        "error_code": result.error.code.value if result.error else None,
        "duration_ms": result.duration_ms,
        "blocked": result.blocked,
    }


__all__ = ["ErrorCode", "ExecutionResult", "execution_artifact"]
