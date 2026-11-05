"""复盘记录仓储。

只管存取，不含业务规则 —— 「哪些失败该复盘」「抢占」这些判断在
``app/services/retrospective.py``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invocation import Invocation
from app.models.retrospective import Retrospective


class RetrospectiveRepository:
    """复盘记录的存取。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs: Any) -> Retrospective:
        record = Retrospective(**kwargs)
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def get(self, record_id: int) -> Retrospective | None:
        return await self.session.get(Retrospective, record_id)

    async def list_page(
        self,
        *,
        thread_id: str | None = None,
        status: str | None = None,
        carrier: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[Retrospective]:
        stmt = select(Retrospective).order_by(Retrospective.id.desc())
        if thread_id:
            stmt = stmt.where(Retrospective.thread_id == thread_id)
        if status:
            stmt = stmt.where(Retrospective.status == status)
        if carrier:
            stmt = stmt.where(Retrospective.carrier == carrier)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_page(
        self,
        *,
        thread_id: str | None = None,
        status: str | None = None,
        carrier: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Retrospective)
        if thread_id:
            stmt = stmt.where(Retrospective.thread_id == thread_id)
        if status:
            stmt = stmt.where(Retrospective.status == status)
        if carrier:
            stmt = stmt.where(Retrospective.carrier == carrier)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def set_status(
        self,
        record_id: int,
        status: str,
        *,
        reject_reason: str | None = None,
        **fields: Any,
    ) -> Retrospective | None:
        """更新状态与其余字段。"""
        record = await self.get(record_id)
        if record is None:
            return None
        record.status = status
        if reject_reason is not None:
            record.reject_reason = reject_reason
        for key, value in fields.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def last_for_thread(self, thread_id: str) -> Retrospective | None:
        """这个会话最近一次复盘（用来判冷却期）。"""
        stmt = (
            select(Retrospective)
            .where(Retrospective.thread_id == thread_id)
            .order_by(Retrospective.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def claim_failures(
        self,
        invocation_ids: list[int],
        retrospective_id: int,
    ) -> int:
        """**原子抢占**这些失败记录。

        条件里带 ``retrospective_id IS NULL`` —— 这一条 UPDATE 就是比较并交换：
        只有还没被认领的失败才会被抢走。两个复盘同时看到同一批失败时，
        只有一个能抢到（另一个受影响行数为 0，空跑）。

        Args:
            invocation_ids: 要认领的调用记录 id。
            retrospective_id: 认领给哪次复盘。

        Returns:
            实际认领到的行数。
        """
        if not invocation_ids:
            return 0
        stmt = (
            update(Invocation)
            .where(Invocation.id.in_(invocation_ids))
            .where(Invocation.retrospective_id.is_(None))
            .values(retrospective_id=retrospective_id)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)

    async def list_claimed(
        self,
        retrospective_id: int,
        candidate_ids: list[int],
    ) -> list[Invocation]:
        """这批候选里，**实际被这次复盘抢到**的是哪几条。

        抢占是条件更新（``retrospective_id IS NULL``），受影响行数只告诉我们
        "抢到几条"，不告诉"是哪几条" —— 所以再查一次：候选里指向这次复盘的
        就是抢到的。抢不到的那几条归了别人，不该出现在这次复盘里。
        """
        if not candidate_ids:
            return []
        stmt = (
            select(Invocation)
            .where(Invocation.id.in_(candidate_ids))
            .where(Invocation.retrospective_id == retrospective_id)
            .order_by(Invocation.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def release_failures(self, retrospective_id: int) -> None:
        """把这次复盘认领的失败**放回去**（复盘自己没跑成时）。

        抢占是"先占后干活" —— 中途失败不还回去，那几条失败就永远不会被复盘了。
        """
        stmt = (
            update(Invocation)
            .where(Invocation.retrospective_id == retrospective_id)
            .values(retrospective_id=None)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def count_by_status(self) -> dict[str, int]:
        """按状态统计（度量的第一层）。"""
        stmt = select(Retrospective.status, func.count()).group_by(Retrospective.status)
        result = await self.session.execute(stmt)
        return {str(status): int(count) for status, count in result.all()}

    async def count_by_reject_reason(self) -> dict[str, int]:
        """按被拒原因统计（度量的第二层：多少条被守卫拦下）。"""
        stmt = (
            select(Retrospective.reject_reason, func.count())
            .where(Retrospective.reject_reason.is_not(None))
            .group_by(Retrospective.reject_reason)
        )
        result = await self.session.execute(stmt)
        return {str(reason): int(count) for reason, count in result.all()}


__all__ = ["RetrospectiveRepository"]
