"""供应商配置的请求/响应模型。

注意：响应模型里**没有 api_key 字段**，只有脱敏串 —— 明文永远不出服务端。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.providers import get_preset
from app.schemas.common import PageMeta


class LLMProviderBase(BaseModel):
    """供应商配置的公共字段。"""

    name: str = Field(min_length=1, max_length=64, description="配置名称，唯一")
    provider: str = Field(default="openai", max_length=32, description="供应商标识，见 /presets")
    base_url: str | None = Field(
        default=None, max_length=255, description="接口地址，留空用预设默认"
    )
    default_model: str = Field(default="", max_length=64, description="默认模型名")
    available_models: list[str] = Field(default_factory=list, description="可选模型列表")
    embedding_model: str = Field(
        default="",
        max_length=128,
        description="向量化模型名，留空表示这条配置不参与向量化",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=1_000_000)
    context_window: int = Field(
        default=0,
        ge=0,
        le=10_000_000,
        description="上下文窗口（token），0=未知。决定对话历史何时被压缩",
    )
    timeout: int = Field(default=60, ge=1, le=3600)
    is_default: bool = Field(default=False, description="是否默认供应商（全局唯一）")
    is_active: bool = Field(default=True)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, value: str) -> str:
        if get_preset(value) is None:
            raise ValueError(f"不支持的供应商 {value}")
        return value


class LLMProviderCreate(LLMProviderBase):
    """创建请求。"""

    api_key: str | None = Field(default=None, max_length=512, description="API Key，加密存储")


class LLMProviderUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, max_length=64)
    available_models: list[str] | None = None
    embedding_model: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    context_window: int | None = Field(
        default=None, ge=0, le=10_000_000, description="上下文窗口（token），0=未知"
    )
    timeout: int | None = Field(default=None, ge=1, le=3600)
    is_default: bool | None = None
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=255)

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, value: str | None) -> str | None:
        if value is not None and get_preset(value) is None:
            raise ValueError(f"不支持的供应商 {value}")
        return value


class LLMProviderOut(BaseModel):
    """响应模型：只给脱敏后的 key。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    base_url: str | None
    default_model: str
    available_models: list[str]
    embedding_model: str = Field(default="", description="向量化模型名")
    embedding_dim: int = Field(default=0, description="向量维度")
    temperature: float
    max_tokens: int
    context_window: int = Field(default=0, description="上下文窗口（token），0=未知")
    timeout: int
    is_default: bool
    is_active: bool
    description: str | None
    api_key_masked: str = Field(default="", description="脱敏后的 API Key")
    has_api_key: bool = Field(default=False, description="是否已配置 API Key")
    created_at: datetime
    updated_at: datetime


class LLMProviderListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[LLMProviderOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


class ProviderPresetOut(BaseModel):
    """供应商预设（供前端下拉）。"""

    key: str
    display_name: str
    langchain_provider: str
    default_base_url: str
    api_key_env: str
    api_key_required: bool
    openai_compatible: bool
    docs_url: str
    context_window: int = Field(default=0, description="该厂商的上下文窗口（token），0=未知")


class ProviderTestOut(BaseModel):
    """连通性测试结果。"""

    ok: bool = Field(description="是否连通")
    provider_id: int
    model: str = Field(default="", description="实际使用的模型")
    latency_ms: int = Field(default=0, description="往返耗时（毫秒）")
    message: str = Field(default="", description="成功时的回复或失败原因")
