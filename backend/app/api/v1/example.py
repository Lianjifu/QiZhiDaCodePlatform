"""示例资源接口：演示 Controller 层的标准写法。

Controller 只做三件事：接收参数 -> 调用 Service -> 转换响应，
不写业务逻辑，也不直接访问数据库。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import get_example_service
from app.schemas.common import ResponseModel
from app.schemas.example import ExampleCreate, ExampleListOut, ExampleOut, ExampleUpdate
from app.services.example import ExampleService

router = APIRouter(prefix="/examples", tags=["示例"])

ServiceDep = Annotated[ExampleService, Depends(get_example_service)]


@router.get("", response_model=ResponseModel[ExampleListOut], summary="分页查询示例")
async def list_examples(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
) -> ResponseModel[ExampleListOut]:
    result = await service.list_examples(page=page, page_size=page_size)
    listing = ExampleListOut(
        items=[ExampleOut.model_validate(item) for item in result.items],
        meta=result.meta,
    )
    return ResponseModel[ExampleListOut](data=listing)


@router.get(
    "/{example_id}",
    response_model=ResponseModel[ExampleOut],
    summary="查询示例详情",
)
async def get_example(
    example_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[ExampleOut]:
    example = await service.get_example(example_id)
    return ResponseModel[ExampleOut](data=ExampleOut.model_validate(example))


@router.post(
    "",
    response_model=ResponseModel[ExampleOut],
    status_code=status.HTTP_201_CREATED,
    summary="创建示例",
)
async def create_example(
    payload: ExampleCreate,
    service: ServiceDep,
) -> ResponseModel[ExampleOut]:
    example = await service.create_example(payload)
    return ResponseModel[ExampleOut](message="创建成功", data=ExampleOut.model_validate(example))


@router.put(
    "/{example_id}",
    response_model=ResponseModel[ExampleOut],
    summary="更新示例",
)
async def update_example(
    example_id: Annotated[int, Path(ge=1)],
    payload: ExampleUpdate,
    service: ServiceDep,
) -> ResponseModel[ExampleOut]:
    example = await service.update_example(example_id, payload)
    return ResponseModel[ExampleOut](message="更新成功", data=ExampleOut.model_validate(example))


@router.delete(
    "/{example_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除示例",
)
async def delete_example(
    example_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_example(example_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
