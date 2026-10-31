"""统一能力清单的响应模型。

能力层的三类成员（tool / mcp / skill）在接口层是同一种东西 ——
一条清单条目。字段按 kind 取用：tool 看 source_type / input_schema，
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
    permission: str = "none"
    enabled: bool = True
    origin: str = "user"
    version: int = 1

    # 仅 tool
    source_type: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict, description="入参 JSON Schema")
    output_schema: dict[str, Any] | None = Field(default=None, description="出参 JSON Schema")
    locator: dict[str, Any] = Field(default_factory=dict)

    # 治理策略
    risk: str = "read"
    timeout_s: int = 30
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)

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


class CapabilitySpecOut(BaseModel):
    """能力的完整声明：契约 + 策略。"""

    key: str
    kind: str = "tool"
    display_name: str = ""
    description: str = ""
    enabled: bool = True
    source_type: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    risk: str = "read"
    permission: str = "none"
    timeout_s: int = 30
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    trust_mode: str = Field(default="balanced", description="当前会话的信任模式")


class CapabilityInvokeOut(BaseModel):
    """单次能力调用的结果。"""

    ok: bool
    key: str
    content: str = ""
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    duration_ms: int = 0
    invocation_id: int | None = Field(default=None, description="调用记录 ID")
    status: str = Field(default="succeeded", description="pending（待审批）/ succeeded / failed")


class SkillPreviewOut(BaseModel):
    """技能预览：挂上去会带来哪些能力、什么提示词。"""

    skill: str
    knowledge: str = ""
    checklist: list[str] = Field(default_factory=list)
    constraints: str = ""
    members: list[str] = Field(default_factory=list)
    prompt: str = Field(default="", description="组装后的提示词片段")


class InvocationOut(BaseModel):
    """一次调用的记录。"""

    id: int
    thread_id: str | None = None
    capability_key: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    risk: str = "read"
    permission: str = "none"
    decision_reason: str = ""
    stages: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    created_at: str = ""
    decided_at: str | None = None
    finished_at: str | None = None


class InvocationListOut(BaseModel):
    """调用记录分页。"""

    items: list[InvocationOut] = Field(default_factory=list)
    total: int = 0
    pending: int = Field(default=0, description="还在等审批的条数")
