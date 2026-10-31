"""工具（原子能力）定义。

三类来源：
- ``builtin``：平台内置的 Python 函数，用 ``source`` 指向``模块:工厂函数``；
- ``http``：外部 REST 接口，``config`` 里放 method/url/headers/body 模板；
- ``mcp``：来自某个 MCP server，``source`` 存 server 名，``config.tool_name`` 存工具名。

声明字段（风险等级、权限要求、超时、重试策略、工具注解、输出契约）是**注册层
的声明**，不是运行时判定 —— 执行层按这些声明 + 参数级规则做最终判断。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

TOOL_TYPES = ("builtin", "http", "mcp")

# 权限要求：谁能放行这次调用（none | confirm | admin）
PERMISSION_NONE = "none"
PERMISSION_CONFIRM = "confirm"
PERMISSION_ADMIN = "admin"

# 来源：出厂内置 or 用户自建
ORIGIN_BUILTIN = "builtin"
ORIGIN_USER = "user"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Tool(Base):
    """一个可被 agent 调用的原子能力。"""

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
    # builtin | user —— 内置能力由出厂源安装，user 是用户自建
    origin: Mapped[str] = mapped_column(String(16), default=ORIGIN_USER, nullable=False)
    # 检索用的领域标签：文件 / 网络 / 数据库 / 代码 …
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 权限要求：none | confirm | admin
    permission: Mapped[str] = mapped_column(String(16), default=PERMISSION_NONE, nullable=False)
    # 风险等级：read | write | execute | network | destructive
    risk: Mapped[str] = mapped_column(String(16), default="read", nullable=False)
    # 输出契约（JSON Schema）；为空表示该能力返回自由文本
    output_schema: Mapped[dict | None] = mapped_column(JSON, default=None, nullable=True)
    # 单次调用超时（秒）
    timeout_s: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    # 重试策略：{max_attempts, backoff_s, retry_on}
    retry_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 工具注解（对齐 MCP tool annotations）：{read_only, destructive, idempotent, open_world}
    annotations: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
