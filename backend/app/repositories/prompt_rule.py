"""提示词规则仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_rule import PromptRule


class PromptRuleRepository:
    """提示词规则的存取。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs: Any) -> PromptRule:
        rule = PromptRule(**kwargs)
        self.session.add(rule)
        await self.session.flush()
        await self.session.refresh(rule)
        return rule

    async def get(self, rule_id: int) -> PromptRule | None:
        return await self.session.get(PromptRule, rule_id)

    async def list_enabled(self) -> list[PromptRule]:
        """启用中的规则 —— 进提示词的就是这些。

        **按 id 排**（不按更新时间）：图缓存键里带的就是这个顺序，
        顺序变了等于换了一张图，没必要。
        """
        stmt = (
            select(PromptRule)
            .where(PromptRule.enabled.is_(True))
            .order_by(PromptRule.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, *, offset: int = 0, limit: int = 50) -> list[PromptRule]:
        stmt = select(PromptRule).order_by(PromptRule.id.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(PromptRule)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def set_enabled(self, rule_id: int, enabled: bool) -> PromptRule | None:
        """启用 / 停用 —— 关掉就是撤销（不用改代码、不用发版）。"""
        stmt = update(PromptRule).where(PromptRule.id == rule_id).values(enabled=enabled)
        await self.session.execute(stmt)
        await self.session.flush()
        return await self.get(rule_id)

    async def delete_by_retrospective(self, retrospective_id: int) -> int:
        """删掉某次复盘产生的全部规则（撤销用）。"""
        from sqlalchemy import delete

        stmt = delete(PromptRule).where(PromptRule.retrospective_id == retrospective_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)


__all__ = ["PromptRuleRepository"]
