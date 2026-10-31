"""示例仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.example import Example
from app.repositories.base import BaseRepository


class ExampleRepository(BaseRepository[Example]):
    model = Example

    async def get_by_name(self, name: str) -> Example | None:
        stmt = select(Example).where(Example.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
