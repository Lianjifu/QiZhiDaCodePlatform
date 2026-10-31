"""工具管理接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import get_tool_service
from app.schemas.common import ResponseModel
from app.schemas.tool import (
    BuiltinToolOut,
    ToolCreate,
    ToolListOut,
    ToolOut,
    ToolTestOut,
    ToolUpdate,
)
from app.services.tool import ToolService
from app.skills.builtin import list_builtin_tools

router = APIRouter(prefix="/tools", tags=["工具"])

ServiceDep = Annotated[ToolService, Depends(get_tool_service)]


@router.get(
    "/builtins", response_model=ResponseModel[list[BuiltinToolOut]], summary="内置工具白名单"
)
async def list_builtins() -> ResponseModel[list[BuiltinToolOut]]:
    """列出可用的内置工具。builtin 类型的工具只能引用这里的入口点。"""
    items = [
        BuiltinToolOut(
            key=spec.key,
            source=spec.source,
            display_name=spec.display_name,
            description=spec.description,
        )
        for spec in list_builtin_tools()
    ]
    return ResponseModel[list[BuiltinToolOut]](data=items)


@router.get("", response_model=ResponseModel[ToolListOut], summary="分页查询工具")
async def list_tools(
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
    type: Annotated[str | None, Query(description="按类型过滤：builtin/http/mcp")] = None,
    enabled: Annotated[bool | None, Query(description="按启用状态过滤")] = None,
) -> ResponseModel[ToolListOut]:
    result = await service.list_tools(page=page, page_size=page_size, type=type, enabled=enabled)
    listing = ToolListOut(
        items=[ToolOut.model_validate(item) for item in result.items],
        meta=result.meta,
    )
    return ResponseModel[ToolListOut](data=listing)


@router.post(
    "",
    response_model=ResponseModel[ToolOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增工具",
)
async def create_tool(
    payload: ToolCreate,
    service: ServiceDep,
) -> ResponseModel[ToolOut]:
    tool = await service.create_tool(payload)
    return ResponseModel[ToolOut](message="创建成功", data=ToolOut.model_validate(tool))


@router.get("/{tool_id}", response_model=ResponseModel[ToolOut], summary="查询工具详情")
async def get_tool(
    tool_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[ToolOut]:
    tool = await service.get_tool(tool_id)
    return ResponseModel[ToolOut](data=ToolOut.model_validate(tool))


@router.put("/{tool_id}", response_model=ResponseModel[ToolOut], summary="更新工具")
async def update_tool(
    tool_id: Annotated[int, Path(ge=1)],
    payload: ToolUpdate,
    service: ServiceDep,
) -> ResponseModel[ToolOut]:
    tool = await service.update_tool(tool_id, payload)
    return ResponseModel[ToolOut](message="更新成功", data=ToolOut.model_validate(tool))


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除工具")
async def delete_tool(
    tool_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_tool(tool_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tool_id}/test", response_model=ResponseModel[ToolTestOut], summary="测试工具配置")
async def test_tool(
    tool_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[ToolTestOut]:
    """检查工具能否被构造出来（不真正执行有副作用的动作）。"""
    result = await service.test_tool(tool_id)
    return ResponseModel[ToolTestOut](data=result)
