"""示例资源的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta


class ExampleBase(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="名称")
    description: str | None = Field(default=None, max_length=255, description="描述")


class ExampleCreate(ExampleBase):
    """创建请求。"""


class ExampleUpdate(BaseModel):
    """更新请求，所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


class ExampleOut(ExampleBase):
    """响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ExampleListOut(BaseModel):
    """列表响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[ExampleOut] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)
