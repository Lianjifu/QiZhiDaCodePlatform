"""短期记忆的存储：``memory_item`` 表。

会话内记忆，随 thread 存活 —— 删会话才清（见 ``docs/plans/memory-module.md``）。

**检索用关键词，不用向量**：会话内的记忆数量少（几十条），关键词检索足够，
而且不用为它单独建一个带向量化配置的集合 —— RAG 集合的向量化配置是建集合时
锁死的，为短期记忆建一个会平白多一份"换模型要重建"的负担。

打分用**查询词覆盖率**：命中的查询词占查询词总数的比例。归一化到 0~1，
可比性好。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.models import SCOPE_SESSION, MemoryEntry, content_hash, ensure_utc
from app.models.memory import MemoryItem
from app.rag.stores.sql import tokenize


class SessionMemoryStore:
    """会话内记忆的存储。"""

    scope = SCOPE_SESSION

    def __init__(self, session: AsyncSession, thread_id: str) -> None:
        """构造存储。

        Args:
            session: 数据库会话。
            thread_id: 这个存储服务的会话。记忆按它隔离 —— 会话之间互不可见。
        """
        self.session = session
        self.thread_id = thread_id

    # ---------- 写 ----------

    async def add(self, entry: MemoryEntry) -> str:
        """写入一条记忆。

        **同内容不新增，而是合并**：内容哈希一样就更新原条目（强度上调、
        使用次数 +1、刷新时间）—— 记忆库膨胀会让真正需要的那条被噪声淹掉。
        """
        digest = content_hash(entry.content)
        existing = await self._find_by_hash(digest)
        if existing is not None:
            # 同内容 → 更新原条目，不新增。
            # 注意**不动 use_count** —— 那个字段是"被召回过几次"，
            # 写入不是召回。
            existing.content = entry.content
            if entry.title:
                existing.title = entry.title
            if entry.tags:
                existing.tags = list(entry.tags)
            existing.strength = max(existing.strength, entry.strength)
            existing.last_used_at = datetime.now(UTC)
            await self.session.flush()
            return f"s:{existing.id}"

        row = MemoryItem(
            thread_id=self.thread_id,
            title=entry.title,
            content=entry.content,
            kind=entry.kind,
            tags=list(entry.tags),
            strength=entry.strength,
            use_count=entry.use_count,
            source=entry.source,
            content_hash=digest,
        )
        self.session.add(row)
        await self.session.flush()
        return f"s:{row.id}"

    # ---------- 读 ----------

    async def get(self, item_id: str) -> MemoryEntry | None:
        """取一条记忆。"""
        row_id = _parse_id(item_id)
        if row_id is None:
            return None
        stmt = select(MemoryItem).where(
            MemoryItem.id == row_id, MemoryItem.thread_id == self.thread_id
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_entry(row) if row is not None else None

    async def search(self, query: str, top_k: int, **_: object) -> list[MemoryEntry]:
        """关键词召回。

        **已被取代的记忆不参与召回** —— 它还在（管理接口能看见），只是不再
        出现在模型面前。

        先用数据库过滤掉明显不相关的（避免把整表拉进内存），再在 Python 里
        算覆盖率排序。
        """
        tokens = tokenize(query)
        if not tokens:
            return []
        conditions = []
        for token in tokens[:16]:
            pattern = f"%{token}%"
            conditions.append(
                or_(
                    MemoryItem.title.ilike(pattern),
                    MemoryItem.content.ilike(pattern),
                )
            )
        stmt = (
            select(MemoryItem)
            .where(
                MemoryItem.thread_id == self.thread_id,
                MemoryItem.superseded.is_(False),
                or_(*conditions),
            )
            .order_by(MemoryItem.updated_at.desc())
            .limit(max(top_k * 5, 20))
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        scored: list[MemoryEntry] = []
        for row in rows:
            entry = _to_entry(row)
            entry.relevance = _coverage(tokens, row)
            if entry.relevance > 0:
                scored.append(entry)
        scored.sort(key=lambda item: item.relevance, reverse=True)
        return scored[:top_k]

    async def list_all(self, limit: int = 100) -> list[MemoryEntry]:
        """列出本会话的全部记忆。"""
        stmt = (
            select(MemoryItem)
            .where(MemoryItem.thread_id == self.thread_id)
            .order_by(MemoryItem.updated_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_entry(row) for row in rows]

    # ---------- 更新 / 删除 ----------

    async def touch(self, item_id: str) -> None:
        """命中即重新激活。"""
        row_id = _parse_id(item_id)
        if row_id is None:
            return
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id == row_id, MemoryItem.thread_id == self.thread_id)
            .values(use_count=MemoryItem.use_count + 1, last_used_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def supersede(self, item_id: str) -> bool:
        """标记一条记忆已被取代。

        **不删除** —— 取代关系是模型判断的，它可能判断错。标记之后这条记忆
        不再参与召回，但还在库里（管理接口能看见）。
        """
        row_id = _parse_id(item_id)
        if row_id is None:
            return False
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id == row_id, MemoryItem.thread_id == self.thread_id)
            .values(superseded=True, updated_at=datetime.now(UTC))
            .returning(MemoryItem.id)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none() is not None

    async def forget(self, item_id: str) -> bool:
        """删除一条记忆。"""
        row_id = _parse_id(item_id)
        if row_id is None:
            return False
        stmt = (
            delete(MemoryItem)
            .where(MemoryItem.id == row_id, MemoryItem.thread_id == self.thread_id)
            .returning(MemoryItem.id)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none() is not None

    async def forget_thread(self, thread_id: str) -> int:
        """删掉一个会话的全部记忆。

        会话被删时调用 —— 短期记忆与对话历史**同生共死**。
        """
        stmt = delete(MemoryItem).where(MemoryItem.thread_id == thread_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)

    # ---------- 内部 ----------

    async def _find_by_hash(self, digest: str) -> MemoryItem | None:
        stmt = select(MemoryItem).where(
            MemoryItem.thread_id == self.thread_id,
            MemoryItem.content_hash == digest,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _parse_id(item_id: str) -> int | None:
    """从 ``s:12`` 里取出 12。格式不对返回 ``None``。"""
    raw = (item_id or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    try:
        return int(raw)
    except ValueError:
        return None


def _coverage(query_tokens: list[str], row: MemoryItem) -> float:
    """查询词覆盖率：命中的查询词占查询词总数的比例。

    标签也参与匹配 —— 标签是用户会搜的词，命中标签同样说明这条相关。
    """
    haystack = set(tokenize(row.title))
    haystack.update(tokenize(row.content))
    for tag in row.tags or []:
        haystack.update(tokenize(str(tag)))
    if not haystack:
        return 0.0
    hits = sum(1 for token in query_tokens if token in haystack)
    return hits / len(query_tokens)


def _to_entry(row: MemoryItem) -> MemoryEntry:
    """ORM 行 → 领域结构。"""
    return MemoryEntry(
        id=f"s:{row.id}",
        scope=SCOPE_SESSION,
        title=row.title,
        content=row.content,
        kind=row.kind,
        tags=list(row.tags or []),
        strength=row.strength,
        use_count=row.use_count,
        source=row.source,
        source_thread_id=row.thread_id,
        superseded=bool(row.superseded),
        created_at=ensure_utc(row.created_at),
        last_used_at=ensure_utc(row.last_used_at),
    )


__all__ = ["SessionMemoryStore"]
