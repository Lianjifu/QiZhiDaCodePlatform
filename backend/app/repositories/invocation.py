"""调用记录仓储。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, func, select

from app.models.invocation import FAILURE_STATUSES, Invocation, is_failure
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

    async def list_recent_any(
        self,
        thread_id: str | None,
        *,
        limit: int = 20,
        capability_key: str | None = None,
    ) -> list[Invocation]:
        """最近的调用记录，**新的在前**，不筛状态。

        给反思用 —— agent 得知道自己失败过，否则它会在同一个坑里再摔一次。

        **为什么不在这里筛状态**：什么算"失败"有两层 —— 记录的状态（失败、
        被拒、被阻塞）只是一层；另一层是"工具跑成了但**要做的事没做成**"
        （退出码非零）。后者的判断要看结果里的退出码，放在 Python 里做更合适
        （见 ``app/agents/reflection.py``），也省得 SQL 里写 JSON 查询。

        **按窗口取而不是按时间取**：窗口天然把陈旧的失败挤出去 —— 后面又跑
        了几十次调用，早先那次失败就不在最近 20 次里了，不用引入"多久算
        过期"这种拍脑袋的时间阈值。

        只查 ``thread_id`` 匹配的记录：别人的会话的失败跟本会话无关，
        混进来只会误导。
        """
        stmt = select(Invocation).order_by(Invocation.id.desc()).limit(limit)
        if thread_id:
            stmt = stmt.where(Invocation.thread_id == thread_id)
        if capability_key:
            stmt = stmt.where(Invocation.capability_key == capability_key)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_failures_for_capabilities(
        self,
        thread_id: str,
        capability_keys: list[str],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """这些能力在某个时间窗里失败了几次。

        给"改进有没有用"算复发率用：同一批能力，在改进**之前**失败过几次、
        在改进**之后**又失败过几次 —— 后者比前者少，才说明改进起作用了。

        Args:
            thread_id: 会话 ID。
            capability_keys: 关注哪些能力。
            since: 只算这个时间之后的（不含）。
            until: 只算这个时间之前的（不含）。

        Returns:
            失败次数。
        """
        if not capability_keys:
            return 0
        stmt = (
            select(func.count())
            .select_from(Invocation)
            .where(Invocation.thread_id == thread_id)
            .where(Invocation.capability_key.in_(capability_keys))
            .where(Invocation.status.in_(FAILURE_STATUSES))
        )
        if since is not None:
            stmt = stmt.where(Invocation.created_at > since)
        if until is not None:
            stmt = stmt.where(Invocation.created_at <= until)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list_uncovered_failures(
        self,
        thread_id: str,
        *,
        since: datetime,
        limit: int = 10,
        scan: int = 60,
    ) -> list[Invocation]:
        """这个会话里**还没被复盘过**的失败调用。

        条件：属于本会话、还没被任何复盘认领（``retrospective_id IS NULL``）、
        创建时间在 ``since`` 之后。

        **"失败"的判定放在 Python 里做**，不在 SQL 里 ——
        因为退出码非零的调用在记录里是 ``succeeded``（工具履行了"跑代码并把
        退出码带回来"的职责），只按状态筛会把代码 agent **最常见的那种失败**
        整个漏掉。判定用 ``app/models/invocation.py::is_failure``，
        与反思块、复发率统计同一份。

        所以这里多取一些（``scan`` 条）再筛 —— 免得最近的几条恰好都是成功的。

        **按时间从早到晚排** —— 复盘一次最多看 ``limit`` 条，早的先复盘。
        反过来（取最新的）会让水位线跳过去，中间没复盘过的就永远漏了。

        Args:
            thread_id: 会话 ID。
            since: 只看这个时间之后的失败（早于它的多半是老账，不该翻）。
            limit: 最多返回几条失败。
            scan: 往前扫多少条记录来找失败。

        Returns:
            失败调用记录（从早到晚）。
        """
        stmt = (
            select(Invocation)
            .where(Invocation.thread_id == thread_id)
            .where(Invocation.retrospective_id.is_(None))
            .where(Invocation.created_at >= since)
            .order_by(Invocation.id.desc())
            .limit(scan)
        )
        result = await self.session.execute(stmt)
        records = list(result.scalars().all())
        records.reverse()
        failures = [record for record in records if is_failure(record)]
        return failures[:limit]

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
