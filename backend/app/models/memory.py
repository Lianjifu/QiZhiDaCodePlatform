"""记忆条目（短期记忆）。

**为什么短期记忆要自己一张表，而不是放在 checkpoint 的 state 里**：
能力（``memory_write``）要能写入记忆，而 checkpoint 的 state 只有图能改 ——
能力函数拿不到它。

**为什么短期记忆不落进程内存**：审批恢复是**另一次 HTTP 请求**，可能落在另一个
进程上（见 ``app/agents/checkpointer.py`` 同样的理由）。记忆必须跨请求可读。

**这张表只放会话内的记忆**（随 thread 存活，删会话才清）。跨会话的长期记忆
落在 RAG 的专用集合里，不在这张表 —— 两种存储的检索与更新方式完全不同，
硬塞进一张表会让两边都别扭。

生命周期与对话历史**同生共死**：单独给记忆设 TTL 会造成"对话历史里明明有、
记忆里没了"的失忆，比没有记忆更糟。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class MemoryItem(Base):
    """一条会话内记忆。"""

    __tablename__ = "memory_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 所属会话。会话被删时，这个会话的记忆一起删
    thread_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # 一句话摘要 —— 目录行就取它，不用读全文
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # 正文（自包含：脱离上下文也能读懂）
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 记忆类型：preference / decision / convention / fact / profile
    kind: Mapped[str] = mapped_column(String(16), default="note", index=True, nullable=False)
    # 检索用的标签 —— 用用户会搜的词，不是内部分类词
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 重要性 0~1，写入时定
    strength: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # 被召回过几次（命中即重新激活的依据）
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 来源：user（用户说的）/ model-inferred（模型推断的）
    source: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    # 已被更新的记忆取代。
    #
    # **不删除，只标记** —— 取代关系是模型判断出来的，它可能判断错（两条
    # 同标签但其实两回事的记忆被误判成"改主意"）。标记之后这条记忆不再参与
    # 召回，但还在库里，管理接口能看见、也能人工恢复。删掉就没了。
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 内容哈希：同一会话里内容一样就是同一条，避免重复记
    content_hash: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)

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
    # 最后一次被召回的时间 —— 时间衰减从这儿算，不是从创建时间
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["MemoryItem", "utcnow"]
