"""意图层：把模型的一次 tool call 变成一条可执行、可审计、可拦截的意图。

分工很关键：**模型提议，意图层收敛并封装**。

- 模型（LangGraph）决定"下一步调哪个能力"——它看得见对话历史，这是它不可替代的优势；
- 意图层把这次 tool call 翻译成 `CapabilityIntent`：绑定声明、解析参数、
  附上策略上下文（信任模式、允许的能力集、幂等键）。

意图层**不产生任何副作用**，也**不替模型做"调哪个能力"的最终决策**。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.capabilities.registration.spec import CapabilitySpec


@dataclass
class CapabilityIntent:
    """一次能力调用的意图。意图层与执行层之间的唯一契约。

    意图是**一次**调用，不是多步计划 —— 多步计划由模型的 ReAct 循环产生，
    意图层只负责把其中一步翻译成可执行、可审计、可拦截的形态。

    里面的策略字段是**快照**：生成意图那一刻的判定，执行层不再重新推导。
    这样"审批时看到的风险"和"执行时的风险"必然是同一个，不会中途变。
    """

    capability_key: str  # 要调的能力 key
    arguments: dict[str, Any]  # 调用参数（已去掉值为 None 的项）
    spec: CapabilitySpec  # 绑定的声明（契约 + 策略）

    # ---- 上下文 ----
    thread_id: str = ""  # 会话 ID，进幂等键
    workspace: Path | None = None  # 本次执行的工作区

    # ---- 策略快照（生成意图那一刻的判定，执行层不再重新推导）----
    trust_mode: str = "balanced"  # 会话信任模式快照
    idempotency_key: str | None = None  # 幂等去重键；不幂等的能力没有这个键
    allowed_keys: set[str] | None = None  # 本次允许的能力集；None 表示不限制
    # 已经人工批准，跳过审批阶段（审批接口执行挂起调用时用）
    pre_approved: bool = False
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))  # 意图生成时间

    def __post_init__(self) -> None:
        # 未提供的可选参数（None）直接去掉，让能力实现用自己的默认值。
        # 不这么做的话，`ls(path=None)` 会在路径校验里炸掉，
        # 而不是像 `ls()` 那样列工作区根目录。
        self.arguments = {
            key: value for key, value in (self.arguments or {}).items() if value is not None
        }

    def fingerprint(self) -> str:
        """意图指纹，用于去重与审计。

        只覆盖能力 key 和参数 —— 不含时间戳，所以同样的调用指纹相同，
        可以用来判断"这是不是同一次调用"。
        """
        payload = json.dumps(
            {"key": self.capability_key, "args": self.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        """可落库的意图快照（参数原样，由落库阶段负责脱敏）。

        注意这里**不做脱敏** —— 审批时要按原始参数执行，脱敏了就执行不了了。
        脱敏发生在写 ``invocation`` 表的那一层。
        """
        return {
            "capability_key": self.capability_key,
            "arguments": dict(self.arguments),
            "thread_id": self.thread_id,
            "trust_mode": self.trust_mode,
            "idempotency_key": self.idempotency_key,
            "fingerprint": self.fingerprint(),
        }


def compute_idempotency_key(
    spec: CapabilitySpec,
    arguments: dict[str, Any],
    thread_id: str = "",
) -> str | None:
    """按声明的幂等性派生去重键。

    只有声明了 ``idempotent`` 的能力才生成键 —— 不幂等的能力（如移动文件）
    盲目去重会把"第二次真的要做的事"给吞掉。

    键里带上会话 ID：不同会话的同名调用不该互相命中。

    Args:
        spec: 能力声明。
        arguments: 调用参数。
        thread_id: 会话 ID。

    Returns:
        幂等键；能力不幂等时返回 ``None``。
    """
    if not spec.annotations.idempotent:
        return None
    payload = json.dumps(
        {"key": spec.key, "args": arguments, "thread": thread_id},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


__all__ = ["CapabilityIntent", "compute_idempotency_key"]
