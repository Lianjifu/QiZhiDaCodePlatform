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
        """默认的供应商配置。

        **一条都没标默认时，取第一条启用的。** 前端的模型选择也是这个顺序
        （先找标了默认的，没有就拿列表里第一条）—— 两边不一致的话，界面
        上选的模型跟后端实际用的模型就对不上，状态面板显示的供应商也会是错的。
        """
        stmt = (
            select(LLMProvider)
            .where(LLMProvider.is_default.is_(True), LLMProvider.is_active.is_(True))
            .limit(1)
        )
        result = await self.session.execute(stmt)
        provider = result.scalar_one_or_none()
        if provider is not None:
            return provider
        fallback = (
            select(LLMProvider)
            .where(LLMProvider.is_active.is_(True))
            .order_by(LLMProvider.id)
            .limit(1)
        )
        result = await self.session.execute(fallback)
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
