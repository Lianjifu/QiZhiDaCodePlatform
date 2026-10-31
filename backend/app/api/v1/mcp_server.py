"""MCP 服务器管理接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import get_mcp_server_service
from app.schemas.common import ResponseModel
from app.schemas.mcp_server import (
    MCPServerCreate,
    MCPServerOut,
    MCPServerToolOut,
    MCPServerUpdate,
)
from app.services.mcp_server import MCPServerService

router = APIRouter(prefix="/mcp-servers", tags=["MCP 服务器"])

ServiceDep = Annotated[MCPServerService, Depends(get_mcp_server_service)]


@router.get("", response_model=ResponseModel[list[MCPServerOut]], summary="查询 MCP 服务器列表")
async def list_servers(
    service: ServiceDep,
    enabled: Annotated[bool | None, Query(description="按启用状态过滤")] = None,
) -> ResponseModel[list[MCPServerOut]]:
    result = await service.list_servers(page=1, page_size=100, enabled=enabled)
    return ResponseModel[list[MCPServerOut]](
        data=[MCPServerOut.model_validate(item) for item in result.items]
    )


@router.post(
    "",
    response_model=ResponseModel[MCPServerOut],
    status_code=status.HTTP_201_CREATED,
    summary="新增 MCP 服务器",
)
async def create_server(
    payload: MCPServerCreate,
    service: ServiceDep,
) -> ResponseModel[MCPServerOut]:
    server = await service.create_server(payload)
    return ResponseModel[MCPServerOut](message="创建成功", data=MCPServerOut.model_validate(server))


@router.get("/{server_id}", response_model=ResponseModel[MCPServerOut], summary="查询详情")
async def get_server(
    server_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[MCPServerOut]:
    server = await service.get_server(server_id)
    return ResponseModel[MCPServerOut](data=MCPServerOut.model_validate(server))


@router.put("/{server_id}", response_model=ResponseModel[MCPServerOut], summary="更新 MCP 服务器")
async def update_server(
    server_id: Annotated[int, Path(ge=1)],
    payload: MCPServerUpdate,
    service: ServiceDep,
) -> ResponseModel[MCPServerOut]:
    server = await service.update_server(server_id, payload)
    return ResponseModel[MCPServerOut](message="更新成功", data=MCPServerOut.model_validate(server))


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除 MCP 服务器")
async def delete_server(
    server_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_server(server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{server_id}/tools",
    response_model=ResponseModel[list[MCPServerToolOut]],
    summary="探测服务器上可用的工具",
)
async def list_remote_tools(
    server_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[list[MCPServerToolOut]]:
    """连上 MCP 服务器，把它提供的工具列出来，用于挑选要登记哪些工具。"""
    tools = await service.list_remote_tools(server_id)
    return ResponseModel[list[MCPServerToolOut]](data=[MCPServerToolOut(**tool) for tool in tools])
