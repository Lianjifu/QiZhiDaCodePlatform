"""能力声明。

注册层的一条声明 = 一个能力在平台里的「契约 + 策略」：输入输出 schema、风险等级、
权限要求、超时、重试策略、幂等标记。

清单里三类成员（tool / mcp / skill）共用这一个结构；skill 只用其中一部分字段。

字段与数据库的对应关系见 `app/capabilities/registration/registry.py`：
数据库列 ``parameters`` 对应这里的 ``input_schema``（声明层的叫法更准）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.capabilities.registration.policy import (
    Annotations,
    PermissionLevel,
    RetryPolicy,
    RiskLevel,
)

# 成员种类
KIND_TOOL = "tool"
KIND_MCP = "mcp"
KIND_SKILL = "skill"

# tool 的执行方式
SOURCE_BUILTIN = "builtin"
SOURCE_HTTP = "http"
SOURCE_MCP = "mcp"

# 来源：出厂内置 or 用户自建
ORIGIN_BUILTIN = "builtin"
ORIGIN_USER = "user"


@dataclass
class CapabilitySpec:
    """一条能力声明。"""

    key: str
    kind: str = KIND_TOOL
    db_id: int = 0
    display_name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    permission: str = PermissionLevel.NONE
    enabled: bool = True
    origin: str = ORIGIN_USER
    version: int = 1

    # ---- 仅 tool：怎么执行、契约长什么样 ----
    source_type: str = SOURCE_BUILTIN
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    locator: dict[str, Any] = field(default_factory=dict)

    # ---- 治理策略 ----
    risk: str = RiskLevel.READ
    timeout_s: int = 30
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    annotations: Annotations = field(default_factory=Annotations)

    # ---- 仅 skill ----
    members: list[str] = field(default_factory=list)
    knowledge: str = ""
    checklist: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    constraints: str = ""

    @property
    def is_executable(self) -> bool:
        """是否是可直接调用的原子能力。"""
        return self.kind == KIND_TOOL

    def summary(self) -> str:
        """一句话摘要，给检索排序和前端展示用。"""
        return self.description or self.display_name or self.key


__all__ = [
    "KIND_MCP",
    "KIND_SKILL",
    "KIND_TOOL",
    "ORIGIN_BUILTIN",
    "ORIGIN_USER",
    "SOURCE_BUILTIN",
    "SOURCE_HTTP",
    "SOURCE_MCP",
    "Annotations",
    "CapabilitySpec",
    "PermissionLevel",
    "RetryPolicy",
    "RiskLevel",
]
