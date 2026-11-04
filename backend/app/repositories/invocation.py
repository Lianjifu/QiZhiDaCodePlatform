"""调用记录仓储。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, func, select

from app.models.invocation import Invocation
from app.repositories.base import BaseRepository

# 幂等去重窗口：只在这么短时间内认为"是同一次调用的重放"。
# 窗口太短挡不住节点重放，太长会把用户隔一段时间的合法重复调用也吞掉。
IDEMPOTENCY_WINDOW_SECONDS = 300


class InvocationRepository(BaseRepository[Invocation]):
    model = Invocation

    async def list_page(
        self,
        *,
        thread_id: str | None = None,
        capability_key: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[Invocation]:
        stmt = select(Invocation).order_by(Invocation.id.desc())
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        if capability_key:
            stmt = stmt.where(Invocation.capability_key == capability_key)
        if status:
            stmt = stmt.where(Invocation.status == status)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_page(
        self,
        *,
        thread_id: str | None = None,
        capability_key: str | None = None,
        status: str | None = None,
    ) -> int:
        stmt = select(Invocation)
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        if capability_key:
            stmt = stmt.where(Invocation.capability_key == capability_key)
        if status:
            stmt = stmt.where(Invocation.status == status)
        result = await self.session.execute(stmt)
        return len(list(result.scalars().all()))

    async def find_recent_success(
        self,
        idempotency_key: str,
        thread_id: str | None = None,
        window_seconds: int = IDEMPOTENCY_WINDOW_SECONDS,
    ) -> Invocation | None:
        """找一条最近的、成功的同键调用。

        命中说明这次调用是重放（比如审批挂起后节点重跑），直接复用它的结果，
        不再执行一遍副作用。
        """
        since = datetime.now(UTC) - timedelta(seconds=window_seconds)
        stmt = (
            select(Invocation)
            .where(Invocation.idempotency_key == idempotency_key)
            .where(Invocation.status == "succeeded")
            .where(Invocation.created_at >= since)
            .order_by(Invocation.id.desc())
            .limit(1)
        )
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def has_pending(self, thread_id: str | None) -> bool:
        """这个会话有没有还在等审批的调用。"""
        stmt = select(Invocation).where(Invocation.status == "pending").limit(1)
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        result = await self.session.execute(stmt)
        return result.scalars().first() is not None

    async def list_stale_pending(self, deadline: datetime) -> list[Invocation]:
        """超时没人处理的 pending 记录。

        后台任务拿它把过期记录置为 ``expired`` —— 不然这些记录会一直挂着，
        `invocation` 表一直在长。

        Args:
            deadline: 早于这个时间创建的才算超时。

        Returns:
            超时的 pending 记录。
        """
        stmt = (
            select(Invocation)
            .where(Invocation.status == "pending")
            .where(Invocation.created_at < deadline)
            .order_by(Invocation.id.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent(self, thread_id: str | None, *, limit: int = 20) -> list[Invocation]:
        """最近的调用记录，**按时间正序**返回。

        给"执行摘要"用：一轮对话结束后，按调用顺序把记录与工具调用对上，
        补上成功 / 失败 / 耗时。
        """
        stmt = select(Invocation).order_by(Invocation.id.desc()).limit(limit)
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        result = await self.session.execute(stmt)
        records = list(result.scalars().all())
        records.reverse()
        return records

    async def sum_changes(self, thread_id: str | None) -> tuple[int, int]:
        """这个会话的改动量合计（新增行数, 删除行数）。

        **在数据库里加** —— 状态面板每 30 秒问一次，改动量藏在
        ``result.diff`` 里；把几百条记录拉回 Python 再逐条翻 JSON，
        拉回来的每一行还带着 payload，白白序列化一大圈。
        """
        stmt = select(
            func.coalesce(
                func.sum(
                    func.cast(
                        func.json_extract(Invocation.result, "$.diff.added"),
                        Integer,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    func.cast(
                        func.json_extract(Invocation.result, "$.diff.removed"),
                        Integer,
                    )
                ),
                0,
            ),
        )
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        row = (await self.session.execute(stmt)).one()
        return int(row[0] or 0), int(row[1] or 0)
