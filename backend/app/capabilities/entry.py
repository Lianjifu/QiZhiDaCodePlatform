"""能力清单条目。

能力层有三类成员，它们在编排层看来是同一套东西 —— 一条**清单条目**：

- ``tool``：原子能力，最小可执行单元；
- ``mcp``：供给通道，一个外部 server 提供的一组能力；
- ``skill``：能力组合，知识 + 一组原子能力。

这里定义的是**视图**（manifest entry），不是数据库表 —— 真正的表仍然是
tool / mcp_server / skill 三张，由 registry 层聚合成统一清单。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 成员种类
KIND_TOOL = "tool"
KIND_MCP = "mcp"
KIND_SKILL = "skill"

# tool 的执行方式
SOURCE_BUILTIN = "builtin"
SOURCE_HTTP = "http"
SOURCE_MCP = "mcp"

# 风险等级
PERMISSION_LOW = "low"
PERMISSION_HIGH = "high"

# 来源
ORIGIN_BUILTIN = "builtin"
ORIGIN_USER = "user"


@dataclass
class CapabilityEntry:
    """统一能力清单里的一条。"""

    key: str
    kind: str = KIND_TOOL
    db_id: int = 0
    display_name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    permission: str = PERMISSION_LOW
    enabled: bool = True
    origin: str = ORIGIN_USER
    version: int = 1

    # ---- 仅 tool ----
    source_type: str = SOURCE_BUILTIN
    parameters: dict = field(default_factory=dict)
    locator: dict = field(default_factory=dict)

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
