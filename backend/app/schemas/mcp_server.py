"""MCP 服务器的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Transport = Literal["stdio", "sse", "streamable_http"]


class MCPServerBase(BaseModel):
    """MCP 服务器配置的公共字段。"""

    name: str = Field(min_length=1, max_length=64, description="服务器名称，唯一")
    description: str | None = Field(default=None, max_length=2000)
    transport: Transport = Field(default="stdio", description="连接方式")
    command: str | None = Field(default=None, max_length=512, description="stdio 形态的命令")
    args: list[str] = Field(default_factory=list, description="stdio 形态的命令参数")
    env: dict[str, str] = Field(default_factory=dict, description="stdio 形态的环境变量")
    url: str | None = Field(default=None, max_length=512, description="sse/http 形态的地址")
    headers: dict[str, str] = Field(default_factory=dict, description="sse/http 形态的请求头")
    enabled: bool = Field(default=True)
    timeout: int = Field(default=30, ge=1, le=600, description="连接/调用超时（秒）")
    tags: list[str] = Field(default_factory=list, description="检索用的领域标签")

    @model_validator(mode="after")
    def _check_transport_fields(self) -> MCPServerBase:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio 形态必须提供 command")
        if self.transport in ("sse", "streamable_http") and not self.url:
            raise ValueError(f"{self.transport} 形态必须提供 url")
        return self


class MCPServerCreate(MCPServerBase):
    """创建请求。"""


class MCPServerUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    transport: Transport | None = None
    command: str | None = Field(default=None, max_length=512)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = Field(default=None, max_length=512)
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    timeout: int | None = Field(default=None, ge=1, le=600)
    tags: list[str] | None = None


class MCPServerOut(MCPServerBase):
    """响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int = 1
    created_at: datetime
    updated_at: datetime


class MCPServerToolOut(BaseModel):
    """MCP server 上可用的工具（探测接口返回）。"""

    name: str
    description: str = ""
