"""技能（能力组合）：一组工具 + 一份知识。

技能真正不可替代的价值不是那组工具，而是"怎么用这组工具"的知识 ——
顺序、坑、什么时候该停手。工具是载体，知识才是核心资产，所以除了
``knowledge``，还留了 checklist / examples / constraints 三个可选字段。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Skill(Base):
    """一个技能。"""

    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 核心做法：挂载该技能时追加到 system prompt
    knowledge: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 执行前后的检查项
    checklist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 正反例
    examples: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 约束与禁区
    constraints: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 检索用的领域标签
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 内置技能不允许删除，只允许停用
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
