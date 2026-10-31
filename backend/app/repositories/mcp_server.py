"""MCP 服务器仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.mcp_server import MCPServer
from app.repositories.base import BaseRepository


class MCPServerRepository(BaseRepository[MCPServer]):
    model = MCPServer

    async def get_by_name(self, name: str) -> MCPServer | None:
        stmt = select(MCPServer).where(MCPServer.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[MCPServer]:
        stmt = select(MCPServer).order_by(MCPServer.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
