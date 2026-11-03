"""工具（原子能力）的请求/响应模型。

各类型的必填项在 schema 层就校验掉，避免脏数据进库：

- builtin：``source`` 必须在内置能力白名单里（否则等于允许执行任意 Python 代码）；
- http：``config.url`` 必填，``parameters`` 必须是一份合法的入参 JSON Schema；
- mcp：``source`` 是 MCP 服务器名，``config.tool_name`` 必填。

声明字段（``permission`` / ``risk`` / ``output_schema`` / ``timeout_s`` /
``retry_policy`` / ``annotations``）是**注册层的声明**，不是运行时判定。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.capabilities.registration.builtin import get_builtin_by_source
from app.schemas.common import PageMeta

ToolType = Literal["builtin", "http", "mcp"]

# 权限要求：谁能放行这次调用（与风险等级 risk 是两个维度）
Permission = Literal["none", "confirm", "admin"]
# 风险等级：能力最坏能造成什么
Risk = Literal["read", "write", "execute", "network", "destructive"]

# 工具名会直接进 LLM 的 tool schema，必须是像标识符的名字
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def validate_parameters(parameters: dict[str, Any]) -> None:
    if not isinstance(parameters, dict):
        raise ValueError("parameters 必须是 JSON Schema 对象")
    if "type" not in parameters and "properties" not in parameters:
        raise ValueError("parameters 必须包含 type 或 properties")
    properties = parameters.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ValueError("parameters.properties 必须是对象")


class ToolBase(BaseModel):
    """工具定义的公共字段。"""

    name: str = Field(min_length=1, max_length=64, description="工具名，会进 LLM 的 schema")
    display_name: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=2000, description="给模型看的说明")
    type: ToolType = Field(default="builtin")
    source: str | None = Field(
        default=None, max_length=255, description="builtin: 入口点；mcp: 服务器名"
    )
    config: dict[str, Any] = Field(default_factory=dict, description="类型相关的细节")
    parameters: dict[str, Any] = Field(default_factory=dict, description="入参 JSON Schema")
    tags: list[str] = Field(default_factory=list, description="检索用的领域标签")
    permission: Permission = Field(default="none", description="权限要求：谁能放行这次调用")
    risk: Risk = Field(default="read", description="风险等级：能力最坏能造成什么")
    output_schema: dict[str, Any] | None = Field(
        default=None, description="出参 JSON Schema；为空表示返回自由文本"
    )
    timeout_s: int = Field(default=30, ge=1, le=600, description="单次调用超时（秒）")
    retry_policy: dict[str, Any] = Field(
        default_factory=dict, description="重试策略 {max_attempts, backoff_s, retry_on}"
    )
    annotations: dict[str, Any] = Field(
        default_factory=dict,
        description="工具注解 {read_only, destructive, idempotent, open_world}",
    )
    enabled: bool = Field(default=True)

    @model_validator(mode="after")
    def _check_name(self) -> ToolBase:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                "工具名必须以字母开头，只能包含字母、数字、下划线和连字符"
                "（因为它会直接进 LLM 的 schema）"
            )
        return self

    @model_validator(mode="after")
    def _check_by_type(self) -> ToolBase:
        if self.type == "builtin":
            if not self.source:
                raise ValueError("builtin 类型必须提供 source")
            if get_builtin_by_source(self.source) is None:
                raise ValueError(f"内置能力 {self.source} 不在白名单内")
        elif self.type == "http":
            if not (self.config or {}).get("url"):
                raise ValueError("http 类型必须在 config.url 提供接口地址")
            validate_parameters(self.parameters)
        elif self.type == "mcp":
            if not self.source:
                raise ValueError("mcp 类型必须提供 source（MCP 服务器名）")
            if not (self.config or {}).get("tool_name"):
                raise ValueError("mcp 类型必须在 config.tool_name 提供工具名")
        return self


class ToolCreate(ToolBase):
    """创建请求。"""


class ToolUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    type: ToolType | None = None
    source: str | None = Field(default=None, max_length=255)
    config: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    tags: list[str] | None = None
    permission: Permission | None = None
    risk: Risk | None = None
    output_schema: dict[str, Any] | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=600)
    retry_policy: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _check_name(self) -> ToolUpdate:
        if self.name is not None and not _NAME_PATTERN.match(self.name):
            raise ValueError("工具名必须以字母开头，只能包含字母、数字、下划线和连字符")
        return self

    @model_validator(mode="after")
    def _check_builtin_source(self) -> ToolUpdate:
        if self.source and get_builtin_by_source(self.source) is None:
            # 只有当类型明确是 builtin 时才强制；mcp 类型的 source 是服务器名
            if self.type in (None, "builtin") and ":" in self.source:
                raise ValueError(f"内置能力 {self.source} 不在白名单内")
        return self

    @model_validator(mode="after")
    def _check_parameters(self) -> ToolUpdate:
        # **只有 http 类型才需要入参 schema** —— builtin / mcp 传空字典是正常的。
        # 合并之后是什么类型，由 service 按合并结果再判一次。
        if self.parameters is not None and self.type == "http":
            validate_parameters(self.parameters)
        return self


class ToolOut(BaseModel):
    """响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    description: str
    type: str
    source: str | None
    config: dict[str, Any]
    parameters: dict[str, Any]
    tags: list[str]
    permission: str
    risk: str = "read"
    output_schema: dict[str, Any] | None = None
    timeout_s: int = 30
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    origin: str = Field(default="user", description="builtin 出厂 / user 自建")
    version: int = 1
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ToolListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[ToolOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class BuiltinToolOut(BaseModel):
    """内置能力（白名单）条目。"""

    key: str
    source: str
    display_name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    permission: str = "none"
    risk: str = "read"
    timeout_s: int = 30
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict, description="入参 JSON Schema")
    output_schema: dict[str, Any] | None = None


class ToolTestOut(BaseModel):
    """工具可用性测试结果。"""

    ok: bool
    tool_id: int
    name: str
    message: str = ""
