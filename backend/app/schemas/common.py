"""通用响应包装与分页模型。"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    """统一响应结构。

    用法：`ResponseModel[ExampleOut]` 表示 data 里放的是某个具体的 Schema。
    """

    model_config = ConfigDict(from_attributes=True)

    code: int = Field(default=0, description="业务状态码，0 表示成功")
    message: str = Field(default="ok", description="提示信息")
    data: T | None = Field(default=None, description="业务数据")


class ErrorResponse(BaseModel):
    """统一错误响应结构。"""

    code: int = Field(description="HTTP 状态码")
    error_code: str = Field(description="业务错误码，例如 NOT_FOUND")
    message: str = Field(description="错误描述")
    details: object | None = Field(default=None, description="错误明细")


class PageMeta(BaseModel):
    """分页元信息。"""

    total: int = Field(default=0, description="总条数")
    page: int = Field(default=1, description="当前页码，从 1 开始")
    page_size: int = Field(default=20, description="每页条数")
    pages: int = Field(default=0, description="总页数")


class ErrorDetail(BaseModel):
    """单条字段错误。"""

    loc: list[str] = Field(default_factory=list)
    msg: str | None = None
    type: str | None = None
