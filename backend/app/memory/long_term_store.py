"""长期记忆的存储：RAG 的专用集合。

一条记忆 = 一个 ``rag_document``：

```text
content = 记忆正文
title   = 一句话摘要
source  = "memory:<thread_id>"     ← 标明这是记忆，不是普通语料
meta    = {kind, memory_kind, tags, strength, use_count, source, source_thread_id}
```

分块的 meta 会继承文档 meta，所以 RAG 的 ``filters`` 能按这些字段过滤。

## 更新计数为什么要直接改 meta

``RagService.ingest_document`` 遇到**内容一样**的文档会直接跳过（不重复切分、
不重复向量化）—— 这对普通语料是对的，但记忆需要更新 ``use_count`` 与
``last_used_at``（命中即重新激活的依据）。所以这两件事分开做：

- **正文变了** → 走入库（内容哈希变了，不会被判重）；
- **只是计数变了** → 直接改 ``rag_document.meta``，不重新入库。

## 集合是用户自己建的

RAG 集合的向量化配置（模型 + 维度）是**建集合时锁死**的，建错了只能重建。
所以平台不自动建记忆集合 —— 由用户在 RAG 界面建好、选好 embedding 模型，再把
集合名配到 ``MEMORY_COLLECTION_NAME``。没配就明确报错，不静默失败。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.locking import TaskLock
from app.memory.models import SCOPE_LONG_TERM, SOURCE_USER, MemoryEntry, ensure_utc
from app.memory.session_store import serialized
from app.rag.models import RagDocument


class LongTermMemoryStore:
    """跨会话记忆的存储。"""

    scope = SCOPE_LONG_TERM

    def __init__(
        self,
        session: AsyncSession,
        collection_name: str | None = None,
        lock: TaskLock | None = None,
    ) -> None:
        """构造存储。

        Args:
            session: 数据库会话。
            collection_name: 记忆集合名；不给就用配置里的。
            lock: 数据库锁 —— 工具并行跑时，长期记忆的读写要跟别的工具串行。
        """
        self.session = session
        self.collection_name = (collection_name or settings.memory_collection_name or "").strip()
        self._lock = lock
        self._collection_id: int | None = None

    # ---------- 集合 ----------

    @serialized
    async def collection_id(self) -> int:
        """取记忆集合的 id。

        Raises:
            CapabilityError: 没配集合名，或集合不存在。
        """
        if not self.collection_name:
            raise _not_configured()
        if self._collection_id is not None:
            return self._collection_id
        from app.rag.models import RagCollection

        collection = (
            await self.session.execute(
                select(RagCollection).where(RagCollection.name == self.collection_name)
            )
        ).scalar_one_or_none()
        if collection is None:
            raise _not_found(self.collection_name)
        self._collection_id = collection.id
        return collection.id

    # ---------- 写 ----------

    @serialized
    async def add(self, entry: MemoryEntry) -> str:
        """写入一条记忆（入库成一篇文档）。"""
        collection_id = await self.collection_id()
        from app.rag.service import RagService

        service = RagService(self.session)
        payload = _IngestPayload(
            title=entry.title,
            content=entry.content,
            source=f"memory:{entry.source_thread_id}",
            meta={
                "kind": "memory",
                "memory_kind": entry.kind,
                "tags": list(entry.tags),
                "strength": entry.strength,
                "use_count": entry.use_count,
                "source": entry.source,
                "source_thread_id": entry.source_thread_id,
                "created_at": (entry.created_at or datetime.now(UTC)).isoformat(),
            },
        )
        result = await service.ingest_document(collection_id, payload)
        document = result.document
        if result.skipped:
            # 内容一样：文档已存在，刷新它的 meta（计数与强度）
            await self._update_meta(
                document.id,
                strength=entry.strength,
                source=entry.source,
                tags=list(entry.tags),
                kind=entry.kind,
            )
        return f"l:{document.id}"

    # ---------- 读 ----------

    @serialized
    async def get(self, item_id: str) -> MemoryEntry | None:
        """取一条记忆的全文。"""
        document_id = _parse_id(item_id)
        if document_id is None:
            return None
        document = (
            await self.session.execute(select(RagDocument).where(RagDocument.id == document_id))
        ).scalar_one_or_none()
        if document is None:
            return None
        return _to_entry(document)

    @serialized
    async def search(self, query: str, top_k: int, **_: object) -> list[MemoryEntry]:
        """混合检索（向量 + 关键词）。

        **按文档去重**：同一篇文档可能命中多个分块（长记忆会被切块）—— 一条
        记忆只出现一次，取分数最高的那块。

        过滤在 Python 里做，不靠 RAG 的 ``filters``：那东西匹配的是**分块**
        的 meta，而文档 meta 并没有传给分块器，挂在文档上的 kind 过滤不到。
        """
        collection_id = await self.collection_id()
        from app.rag.service import RagService

        service = RagService(self.session)
        result = await service.search(
            _SearchPayload(
                collection_id=collection_id,
                query=query,
                top_k=top_k * 3,
                mode="hybrid",
            )
        )
        best: dict[int, Any] = {}
        for hit in result.hits:
            document_id = hit.chunk.document_id
            current = best.get(document_id)
            if current is None or hit.score > current.score:
                best[document_id] = hit

        entries: list[MemoryEntry] = []
        for document_id, hit in best.items():
            document = await self._get_document(document_id)
            if document is None:
                continue
            entry = _to_entry(document)
            if not _is_memory_document(document):
                # 集合里混进了不是记忆的文档（用户把集合配错了）
                continue
            if entry.superseded:
                # 已被取代的记忆不参与召回（标记在文档 meta 上，不在分块上，
                # 所以 RAG 的 filters 拦不住，只能在这里过一道）
                continue
            entry.relevance = float(hit.score)
            entries.append(entry)
        return entries[:top_k]

    @serialized
    async def list_all(self, limit: int = 100) -> list[MemoryEntry]:
        """列出集合里的全部记忆。"""
        collection_id = await self.collection_id()
        from app.rag.service import RagService

        service = RagService(self.session)
        documents, _total = await service.list_documents(
            collection_id, page=1, page_size=min(max(1, limit), 100)
        )
        return [_to_entry(document) for document in documents]

    # ---------- 更新 / 删除 ----------

    @serialized
    async def touch(self, item_id: str) -> None:
        """命中即重新激活：使用次数 +1，刷新最后召回时间。"""
        document_id = _parse_id(item_id)
        if document_id is None:
            return
        document = await self._get_document(document_id)
        if document is None:
            return
        meta = dict(document.meta or {})
        meta["use_count"] = int(meta.get("use_count", 0)) + 1
        meta["last_used_at"] = datetime.now(UTC).isoformat()
        document.meta = meta
        await self.session.flush()

    @serialized
    async def supersede(self, item_id: str) -> bool:
        """标记一条记忆已被取代（不删除，只是不再参与召回）。"""
        document_id = _parse_id(item_id)
        if document_id is None:
            return False
        document = await self._get_document(document_id)
        if document is None:
            return False
        meta = dict(document.meta or {})
        meta["superseded"] = True
        meta["superseded_at"] = datetime.now(UTC).isoformat()
        document.meta = meta
        await self.session.flush()
        return True

    @serialized
    async def forget(self, item_id: str) -> bool:
        """删除一条记忆。"""
        document_id = _parse_id(item_id)
        if document_id is None:
            return False
        from app.rag.service import RagService

        service = RagService(self.session)
        try:
            await service.delete_document(document_id)
        except Exception:  # pragma: no cover - 文档已不存在
            return False
        return True

    # ---------- 内部 ----------

    async def _get_document(self, document_id: int) -> RagDocument | None:
        return (
            await self.session.execute(select(RagDocument).where(RagDocument.id == document_id))
        ).scalar_one_or_none()

    async def _update_meta(
        self,
        document_id: int,
        *,
        strength: float,
        source: str,
        tags: list[str],
        kind: str,
    ) -> None:
        document = await self._get_document(document_id)
        if document is None:
            return
        meta = dict(document.meta or {})
        meta["strength"] = max(float(meta.get("strength", 0.0)), strength)
        meta["source"] = source
        meta["tags"] = list(tags)
        meta["memory_kind"] = kind
        document.meta = meta
        await self.session.flush()


class _IngestPayload:
    """入库载荷（只要 RAG 认的那几个属性）。"""

    def __init__(
        self,
        *,
        title: str,
        content: str,
        source: str,
        meta: dict[str, Any],
    ) -> None:
        self.title = title
        self.content = content
        self.source = source
        self.format = "text"
        self.meta = meta


class _SearchPayload:
    """检索载荷。"""

    def __init__(
        self,
        *,
        collection_id: int,
        query: str,
        top_k: int,
        mode: str,
    ) -> None:
        self.collection_id = collection_id
        self.query = query
        self.top_k = top_k
        self.mode = mode
        self.min_score = 0.0
        self.filters: dict[str, Any] = {}


def _parse_id(item_id: str) -> int | None:
    raw = (item_id or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    try:
        return int(raw)
    except ValueError:
        return None


def _to_entry(document: RagDocument) -> MemoryEntry:
    """RAG 文档 → 领域结构。"""
    meta = dict(document.meta or {})
    created_at = _parse_time(meta.get("created_at"))
    return MemoryEntry(
        id=f"l:{document.id}",
        scope=SCOPE_LONG_TERM,
        title=document.title,
        content=document.content,
        kind=str(meta.get("memory_kind") or "note"),
        tags=list(meta.get("tags") or []),
        strength=float(meta.get("strength", 0.5)),
        use_count=int(meta.get("use_count", 0)),
        source=str(meta.get("source") or SOURCE_USER),
        source_thread_id=str(meta.get("source_thread_id") or ""),
        superseded=bool(meta.get("superseded", False)),
        created_at=ensure_utc(created_at or document.created_at),
        last_used_at=ensure_utc(_parse_time(meta.get("last_used_at"))),
    )


def _is_memory_document(document: RagDocument) -> bool:
    """这篇文档是记忆，还是集合里混进来的别的语料。"""
    return str((document.meta or {}).get("kind", "")) == "memory"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _not_configured():
    from app.capabilities.errors import CapabilityError, ErrorCode

    return CapabilityError(
        "未配置长期记忆集合。请在 RAG 界面创建集合（记得选好 embedding 模型），"
        "并把集合名设置到环境变量 MEMORY_COLLECTION_NAME。",
        code=ErrorCode.EXECUTION_ERROR,
    )


def _not_found(name: str):
    from app.capabilities.errors import CapabilityError, ErrorCode

    return CapabilityError(
        f"长期记忆集合 {name!r} 不存在。请在 RAG 界面创建它，或修正 MEMORY_COLLECTION_NAME。",
        code=ErrorCode.EXECUTION_ERROR,
    )


__all__ = ["LongTermMemoryStore"]
