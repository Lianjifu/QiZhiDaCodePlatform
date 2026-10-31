"""工具的请求/响应模型。

各类型的必填项在 schema 层就校验掉，避免脏数据进库：

- builtin：``source`` 必须在内置工具白名单里（否则等于允许执行任意 Python 代码）；
- http：``config.url`` 必填，``parameters`` 必须是一份合法的入参 JSON Schema；
- mcp：``source`` 是 MCP 服务器名，``config.tool_name`` 必填。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageMeta
from app.skills.builtin import get_builtin_by_source

ToolType = Literal["builtin", "http", "mcp"]

# 工具名会直接进 LLM 的 tool schema，必须是像标识符的名字
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _validate_parameters(parameters: dict[str, Any]) -> None:
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
                raise ValueError(f"内置工具 {self.source} 不在白名单内")
        elif self.type == "http":
            if not (self.config or {}).get("url"):
                raise ValueError("http 类型必须在 config.url 提供接口地址")
            _validate_parameters(self.parameters)
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
                raise ValueError(f"内置工具 {self.source} 不在白名单内")
        return self

    @model_validator(mode="after")
    def _check_parameters(self) -> ToolUpdate:
        if self.parameters is not None:
            _validate_parameters(self.parameters)
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
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ToolListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[ToolOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class BuiltinToolOut(BaseModel):
    """内置工具（白名单）条目。"""

    key: str
    source: str
    display_name: str
    description: str


class ToolTestOut(BaseModel):
    """工具可用性测试结果。"""

    ok: bool
    tool_id: int
    name: str
    message: str = ""
