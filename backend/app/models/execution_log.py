"""执行轨迹。

每次能力执行留一条记录：谁（会话）、在什么时候、调用了哪个能力、参数摘要、
耗时、结果状态、失败时的错误码。

用途有三个：给模型提供"刚才发生了什么"的上下文、给人排障、给业务做计费与审计。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutionLog(Base):
    """一次能力执行的记录。"""

    __tablename__ = "execution_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    capability_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # 参数摘要（脱敏后）
    arguments: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ok: Mapped[bool] = mapped_column(default=True, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
