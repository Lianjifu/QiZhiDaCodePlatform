"""工具定义。

三类来源：
- ``builtin``：平台内置的 Python 函数，用 ``source`` 指向 ``模块:函数``；
- ``http``：外部 HTTP 接口，``config`` 里放 method/url/headers/body 模板；
- ``mcp``：来自某个 MCP server，``source`` 存 server 名，``config.tool_name`` 存工具名。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

TOOL_TYPES = ("builtin", "http", "mcp")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Tool(Base):
    """一个可被 agent 调用的工具。"""

    __tablename__ = "tool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # LLM 看到的工具名，必须是合法的标识符
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # builtin | http | mcp
    type: Mapped[str] = mapped_column(String(16), default="builtin", nullable=False)
    # builtin: "模块:函数"；mcp: MCP 服务器名；http: 留空
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 类型相关的细节：内置工具的额外参数、HTTP 的 method/url、mcp 的 tool_name
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 入参 JSON Schema（http 类型必填；其余类型由实现自动推断）
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
