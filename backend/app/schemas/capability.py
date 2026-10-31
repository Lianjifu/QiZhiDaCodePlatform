"""统一能力清单的响应模型。

能力层的三类成员（tool / mcp / skill）在接口层是同一种东西 ——
一条清单条目。字段按 kind 取用：tool 看 source_type / parameters，
skill 看 members / knowledge。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CapabilityOut(BaseModel):
    """一条能力清单条目。"""

    key: str = Field(description="唯一标识")
    kind: str = Field(description="tool / mcp / skill")
    db_id: int = 0
    display_name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    permission: str = "low"
    enabled: bool = True
    origin: str = "user"
    version: int = 1

    # 仅 tool
    source_type: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    locator: dict[str, Any] = Field(default_factory=dict)

    # 仅 skill
    members: list[str] = Field(default_factory=list)
    knowledge: str = ""
    checklist: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    constraints: str = ""


class CapabilityListOut(BaseModel):
    """统一能力清单。"""

    items: list[CapabilityOut] = Field(default_factory=list)
    total: int = 0
    kinds: list[str] = Field(default_factory=list, description="清单里包含哪些种类")


class CapabilityInvokeOut(BaseModel):
    """单次能力调用的结果。"""

    ok: bool
    key: str
    content: str = ""
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    duration_ms: int = 0


class SkillPreviewOut(BaseModel):
    """技能预览：挂上去会带来哪些能力、什么提示词。"""

    skill: str
    knowledge: str = ""
    checklist: list[str] = Field(default_factory=list)
    constraints: str = ""
    members: list[str] = Field(default_factory=list)
    prompt: str = Field(default="", description="组装后的提示词片段")
