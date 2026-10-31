"""MCP 服务器配置。

一个 MCP server 通常提供一组工具。stdio 形态用本地命令启动，
sse / streamable_http 形态连远程地址。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class MCPServer(Base):
    """一个 MCP 服务器。"""

    __tablename__ = "mcp_server"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stdio | sse | streamable_http
    transport: Mapped[str] = mapped_column(String(32), default="stdio", nullable=False)

    # stdio 形态：本地命令
    command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    args: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    env: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # sse / streamable_http 形态：远程地址
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    headers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
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
