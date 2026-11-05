"""自动积累的提示词规则。

## 为什么提示词要"数据化"

代码里的 ``CODE_AGENT_PROMPT`` 永远只由人改 —— 那是 agent 的安全底线，
不该有谁能改（包括 agent 自己）。

但复盘学到的有些经验确实该进提示词：「这个项目要先跑 lint 再跑测试」、
「报错时先看日志不要猜」。这些是**行为准则**，不是安全底线。

所以把这一层拆成两半：

- **代码里的准则** —— 不可变，人改，走发版；
- **这里的规则** —— 数据，由复盘产出、经范围守卫检查后落库，
  在上下文装配时作为独立块追加。

## 一条规则的影响面

规则只影响**做事方式**，不影响权限与审批 —— 那是 ``policy.py`` 与权限模式
管的事，不由经验决定。写进来的规则会被守卫检查（见
``app/agents/autonomy.py``）：碰到审批 / 权限 / 沙箱那一层的直接拒。

## 关掉它就是撤销

``enabled=False`` 的规则不进提示词 —— 撤销一条规则不用改代码、不用发版。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class PromptRule(Base):
    """一条自动积累的提示词规则。"""

    __tablename__ = "prompt_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 规则正文（一句能直接进提示词的话）
    rule: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 关掉就不进提示词 —— 撤销走这里
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 哪次复盘生成的（没有就是人手工加的）
    retrospective_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
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


__all__ = ["PromptRule"]
