"""MCP 服务器服务：CRUD + 远端工具探测。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.capabilities.mcp_client import MCPError, MCPManager
from app.core.exceptions import ConflictError, NotFoundError
from app.models.mcp_server import MCPServer
from app.repositories.mcp_server import MCPServerRepository
from app.repositories.tool import ToolRepository
from app.schemas.common import PageMeta
from app.schemas.mcp_server import MCPServerCreate, MCPServerUpdate
from app.services.base import BaseService, PageResult


class MCPServerService(BaseService[MCPServerRepository]):
    repository = MCPServerRepository

    async def get_server(self, server_id: int) -> MCPServer:
        server = await self.repo.get(server_id)
        if server is None:
            raise NotFoundError(f"MCP 服务器 {server_id} 不存在")
        return server

    async def list_servers(
        self, *, page: int = 1, page_size: int = 20, enabled: bool | None = None
    ) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items = await self.repo.list_all()
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        total = len(items)
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        pages = (total + page_size - 1) // page_size
        return PageResult(
            items=window,
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )

    async def create_server(self, payload: MCPServerCreate) -> MCPServer:
        if await self.repo.get_by_name(payload.name) is not None:
            raise ConflictError(f"MCP 服务器 {payload.name} 已存在")
        try:
            server = await self.repo.create(**payload.model_dump())
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"MCP 服务器 {payload.name} 已存在") from exc
        await self.session.commit()
        return server

    async def update_server(self, server_id: int, payload: MCPServerUpdate) -> MCPServer:
        server = await self.get_server(server_id)
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] and data["name"] != server.name:
            if await self.repo.get_by_name(data["name"]) is not None:
                raise ConflictError(f"MCP 服务器 {data['name']} 已存在")
        server = await self.repo.update(server, **data)
        await self.session.commit()
        return server

    async def delete_server(self, server_id: int) -> None:
        server = await self.get_server(server_id)
        tool_repo = ToolRepository(self.session)
        referencing = [tool for tool in await tool_repo.list_all() if tool.source == server.name]
        if referencing:
            names = "、".join(tool.name for tool in referencing[:5])
            raise ConflictError(f"还有工具引用了这个 MCP 服务器（{names}），请先删除或改掉这些工具")
        await self.repo.delete(server)
        await self.session.commit()

    async def list_remote_tools(self, server_id: int) -> list[dict]:
        """连上 MCP 服务器，把它提供的工具列出来（用于挑选要登记哪些工具）。"""
        server = await self.get_server(server_id)
        if not server.enabled:
            raise ConflictError(f"MCP 服务器 {server.name} 已停用")
        try:
            tools = await MCPManager().list_tools(server)
        except MCPError as exc:
            raise ConflictError(str(exc)) from exc
        return [
            {"name": tool.name, "description": (tool.description or "")[:500]} for tool in tools
        ]
