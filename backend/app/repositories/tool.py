"""工具仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.tool import Tool
from app.repositories.base import BaseRepository


class ToolRepository(BaseRepository[Tool]):
    model = Tool

    async def get_by_name(self, name: str) -> Tool | None:
        stmt = select(Tool).where(Tool.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Tool]:
        stmt = select(Tool).order_by(Tool.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_ids(self, ids: list[int]) -> list[Tool]:
        if not ids:
            return []
        stmt = select(Tool).where(Tool.id.in_(ids)).order_by(Tool.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
