"""调用记录（invocation）。

一次能力调用的**完整生命周期**：谁（会话）、调了哪个能力、参数、评估出的风险与
权限要求、审批结论、各阶段结论、最终结果或错误。

为什么不再叫"执行日志"：它现在不只是流水，还要承载**待审批的状态**（pending），
审批接口要按它把调用执行掉。一张表就是唯一事实来源，省得两张表对不上。

用途：给人排障、给业务做计费与审计、给模型提供"刚才发生了什么"的上下文。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 生命周期状态
STATUS_PENDING = "pending"  # 等待审批
STATUS_APPROVED = "approved"  # 已批准，待执行
STATUS_DENIED = "denied"  # 被拒绝
STATUS_RUNNING = "running"  # 执行中
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_BLOCKED = "blocked"  # 任务阻塞：重试超限或置信度过低，转人工

TERMINAL_STATUSES = (
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_DENIED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_BLOCKED,
)

# **没成功**的状态 —— 反思时喂给 agent 的就是这些。
#
# 不含 ``pending``（还在等人批）与 ``cancelled``（人主动取消）：
# 前者不是失败，后者不是 agent 的错。把它俩算进去，agent 会看到
# "上次 run_python 失败了" —— 其实那次是人自己取消的。
FAILURE_STATUSES = (
    STATUS_FAILED,
    STATUS_BLOCKED,
    STATUS_DENIED,
    STATUS_EXPIRED,
)

# 人工已经处理完的状态（无论是批准、拒绝还是取消）。
DECIDED_STATUSES = (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Invocation(Base):
    """一次能力调用的记录。"""

    __tablename__ = "invocation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    capability_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # 参数（脱敏后）——对外展示用
    arguments: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 原始参数——审批通过后要按它执行，所以不能脱敏；不对外暴露
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 意图指纹：同一意图的重复调用能对上
    intent_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # 幂等键：同键的重复调用不重复执行
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PENDING, index=True, nullable=False
    )
    # **哪次复盘覆盖了这条** —— 失败被复盘过之后，这里指向那一次。
    #
    # 为什么要有这一列：复盘要知道自己看过哪些失败（没复盘过的才该复盘），
    # 而「这条失败有没有在改进之后又发生」是衡量改进有没有用的依据。
    #
    # **不加外键** —— ``retrospective`` 与 ``invocation`` 互相引用会成环，
    # 删表顺序就没法定。这里只存 id，删复盘时把它置空即可。
    retrospective_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # 评估后的风险等级与权限要求
    risk: Mapped[str] = mapped_column(String(16), default="read", nullable=False)
    permission: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    # 为什么放行 / 拦截 / 挂起
    decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 各阶段结论
    stages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 结果（脱敏后）
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
