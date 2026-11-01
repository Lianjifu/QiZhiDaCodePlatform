"""大模型供应商配置。

API Key 只存密文（`api_key_encrypted`），脱敏串（`api_key_masked`）在写入时算好，
列表接口因此不需要解密、不需要主密钥也能展示。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class LLMProvider(Base):
    """一个可用的模型接入配置。"""

    __tablename__ = "llm_provider"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_masked: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    default_model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    available_models: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 向量化模型名（空 = 这条配置不参与向量化）。绝大多数厂商的对话与向量化
    # 接口在同一个 base_url 上，所以复用这一条配置即可，不必再单独建一套
    embedding_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 向量维度（探测出来的），用于建集合时校验
    embedding_dim: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    # 上下文窗口（token）。0 = 未知 —— 不给模型挂 profile，框架用保守默认值。
    #
    # 这个值决定对话历史什么时候被压缩、按什么比例保留。不填的话，
    # deepAgents 只能按固定的"170000 token 触发、保留 6 条消息"来，
    # 对大窗口的模型来说提前太多、保留得太少。
    context_window: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
