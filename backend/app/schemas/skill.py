"""技能（能力组合）的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta


class SkillBase(BaseModel):
    """技能配置的公共字段。"""

    name: str = Field(min_length=1, max_length=64, description="技能名称，唯一")
    description: str | None = Field(default=None, max_length=2000)
    knowledge: str = Field(default="", max_length=8000, description="核心做法，挂载时追加到提示词")
    checklist: list[str] = Field(default_factory=list, description="执行前后的检查项")
    examples: list[str] = Field(default_factory=list, description="正反例")
    constraints: str = Field(default="", max_length=4000, description="约束与禁区")
    tags: list[str] = Field(default_factory=list, description="检索用的领域标签")
    enabled: bool = Field(default=True)


class SkillCreate(SkillBase):
    """创建请求。"""

    tool_ids: list[int] = Field(default_factory=list, description="该技能包含的工具 id")


class SkillUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    knowledge: str | None = Field(default=None, max_length=8000)
    checklist: list[str] | None = None
    examples: list[str] | None = None
    constraints: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = None
    enabled: bool | None = None
    tool_ids: list[int] | None = Field(default=None, description="替换该技能包含的工具 id")


class SkillOut(SkillBase):
    """响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    builtin: bool = Field(default=False, description="内置技能不允许删除")
    version: int = 1
    tool_ids: list[int] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SkillListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[SkillOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)
