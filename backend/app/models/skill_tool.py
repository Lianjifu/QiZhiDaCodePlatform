"""技能与工具的关联表。

用关联表而不是在技能里存 JSON 数组：避免悬挂 id，也方便反查"某个工具被哪些技能引用"。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class SkillTool(Base):
    """技能 ↔ 工具的多对多关联。"""

    __tablename__ = "skill_tool"
    __table_args__ = (UniqueConstraint("skill_id", "tool_id", name="uq_skill_tool"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skill.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tool_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tool.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
