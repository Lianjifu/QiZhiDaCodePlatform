"""技能的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta


class SkillBase(BaseModel):
    """技能配置的公共字段。"""

    name: str = Field(min_length=1, max_length=64, description="技能名称，唯一")
    description: str | None = Field(default=None, max_length=2000)
    prompt: str = Field(
        default="", max_length=8000, description="挂载时追加到 system prompt 的内容"
    )
    enabled: bool = Field(default=True)


class SkillCreate(SkillBase):
    """创建请求。"""

    tool_ids: list[int] = Field(default_factory=list, description="该技能包含的工具 id")


class SkillUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    prompt: str | None = Field(default=None, max_length=8000)
    enabled: bool | None = None
    tool_ids: list[int] | None = Field(default=None, description="替换该技能包含的工具 id")


class SkillOut(SkillBase):
    """响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    builtin: bool = Field(default=False, description="内置技能不允许删除")
    tool_ids: list[int] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SkillListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[SkillOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)
