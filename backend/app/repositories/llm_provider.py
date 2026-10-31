"""供应商配置仓储。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.llm_provider import LLMProvider
from app.repositories.base import BaseRepository


class LLMProviderRepository(BaseRepository[LLMProvider]):
    model = LLMProvider

    async def get_by_name(self, name: str) -> LLMProvider | None:
        stmt = select(LLMProvider).where(LLMProvider.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_default(self) -> LLMProvider | None:
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_default.is_(True), LLMProvider.is_active.is_(True))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[LLMProvider]:
        stmt = select(LLMProvider).order_by(LLMProvider.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def clear_default_except(self, keep_id: int) -> None:
        """把默认标记收敛到唯一一条。"""
        others = await self.list_all()
        for other in others:
            if other.id != keep_id and other.is_default:
                other.is_default = False
        await self.session.flush()
