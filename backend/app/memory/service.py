"""记忆服务：两种存储的统一入口。

职责：

- **按 scope 分发**（会话内 → 表，跨会话 → RAG 集合）；
- **打分**：检索相关度 × 时间衰减 × 使用次数；
- **命中即重新激活**；
- **写入校验**：凭据硬拦、重复合并、同主题冲突要求显式覆盖。

## 为什么写入校验在服务层，不在能力实现里

模型决定"要不要记"，但校验不能指望模型每次都做对。放在服务层，任何入口
（能力、管理接口）都走同一套规则 —— 不会出现"从 A 入口写能绕开检查"。

## 为什么冲突要"要求显式覆盖"而不是自动合并

用户改主意时，不能悄悄新增一条让两条打架 —— 召回时模型会看到两个互相矛盾
的记忆，无从判断该信哪个。要么明确 ``supersedes`` 指名覆盖谁，要么不写。
"""

from __future__ import annotations

import asyncio

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.redaction import contains_secret
from app.core.config import settings
from app.memory.catalog import MemoryCatalog, build_catalog
from app.memory.models import (
    KIND_NOTE,
    MEMORY_KINDS,
    SCOPE_ALL,
    SCOPE_LONG_TERM,
    SCOPE_SESSION,
    SOURCE_MODEL,
    MemoryEntry,
    content_hash,
    effective_score,
)
from app.memory.session_store import SessionMemoryStore
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# 单条记忆正文的长度上限（字符）
MAX_CONTENT_CHARS = 2000
# 单条摘要的长度上限
MAX_TITLE_CHARS = 120


class MemoryService:
    """记忆的统一入口。"""

    def __init__(
        self,
        session: AsyncSession | None = None,
        thread_id: str = "",
        long_term_store: MemoryStore | None = None,
        db_lock: asyncio.Lock | None = None,
    ) -> None:
        """构造服务。

        Args:
            session: 数据库会话（短期记忆要用）。
            thread_id: 当前会话。
            long_term_store: 长期记忆的存储；没配就只支持会话内记忆。
            db_lock: 数据库锁 —— 工具并行跑时，记忆的读写要跟别的工具串行。
        """
        self.session = session
        self.thread_id = thread_id
        self.long_term_store = long_term_store
        self.db_lock = db_lock
        self._session_store = (
            SessionMemoryStore(session, thread_id, lock=db_lock)
            if session is not None
            else None
        )

    def for_thread(self, thread_id: str) -> MemoryService:
        """返回一个绑定了会话的服务副本。

        会话内的存储是在构造时按 ``thread_id`` 建的，所以换会话要重新造一个。
        长期记忆的存储是共用的（它本来就跨会话）。
        """
        return MemoryService(
            session=self.session,
            thread_id=thread_id,
            long_term_store=self.long_term_store,
            db_lock=self.db_lock,
        )

    # ---------- 召回 ----------

    async def recall_catalog(
        self,
        query: str,
        *,
        budget_chars: int | None = None,
        max_entries: int | None = None,
    ) -> MemoryCatalog:
        """召回并组装成目录。

        这是**每轮自动注入上下文**用的入口 —— 只给目录，不给正文。

        目录同时收**会话内**与**长期**记忆：会话内的往往最相关，长期的提供
        跨会话的经验。长期记忆没配集合时降级成"只有会话内记忆"，不能因此
        让整个目录失败。

        Args:
            query: 检索词（一般用用户这轮说的话）。
            budget_chars: 目录块的字符预算。
            max_entries: 最多几条。

        Returns:
            装好的目录。空目录不占上下文。
        """
        if not settings.memory_catalog_enabled:
            return MemoryCatalog()

        limit = max_entries or settings.memory_catalog_max_entries
        raw: list[MemoryEntry] = []

        if self._session_store is not None and self.thread_id:
            raw.extend(await self._session_store.search(query, top_k=limit * 2))

        if self.long_term_store is not None:
            try:
                raw.extend(await self.long_term_store.search(query, top_k=limit * 2))
            except CapabilityError as exc:
                # 长期记忆没配集合是最常见的情况，不该让整个目录失败
                logger.info("长期记忆不可用，目录里只有会话内记忆: %s", exc)

        scored = _apply_scoring(raw)
        return build_catalog(
            scored,
            budget_chars=budget_chars or settings.memory_catalog_budget_chars,
            max_entries=limit,
            summary_max_chars=settings.memory_summary_max_chars,
        )

    async def recall(
        self,
        query: str,
        *,
        scope: str = SCOPE_SESSION,
        top_k: int = 8,
    ) -> list[MemoryEntry]:
        """召回记忆。

        **召回资格不受权重影响** —— 只要没被删除的记忆都在候选池里，权重只
        参与排序。降权 ≠ 淘汰。

        Args:
            query: 检索词。
            scope: ``session`` / ``long_term`` / ``all``（两个都搜）。
            top_k: 最多返回几条。

        Returns:
            按综合分排好序的记忆（带 ``relevance``）。
        """
        scopes = (SCOPE_SESSION, SCOPE_LONG_TERM) if scope == SCOPE_ALL else (scope,)
        hits: list[MemoryEntry] = []
        for one in scopes:
            store = self._store_for(one)
            if store is None:
                continue
            raw = await store.search(query, top_k=top_k * 3)
            hits.extend(_apply_scoring(raw))
        hits.sort(key=lambda item: item.relevance, reverse=True)
        hits = hits[:top_k]
        # 命中即重新激活：衰减从"最后一次被召回"重新算
        for entry in hits:
            store = self._store_for(entry.scope)
            if store is not None:
                await store.touch(entry.id)
        return hits

    async def read(self, item_id: str) -> MemoryEntry | None:
        """取一条记忆的全文。"""
        store = self._store_for(_scope_of(item_id))
        if store is None:
            return None
        return await store.get(item_id)

    async def list_all(
        self,
        *,
        scope: str = SCOPE_SESSION,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """列出记忆（管理接口用）。"""
        store = self._store_for(scope)
        if store is None:
            return []
        return await store.list_all(limit=limit)

    # ---------- 写入 ----------

    async def remember(
        self,
        *,
        content: str,
        kind: str = KIND_NOTE,
        tags: list[str] | None = None,
        scope: str = SCOPE_SESSION,
        title: str = "",
        supersedes: str | None = None,
        source: str = SOURCE_MODEL,
    ) -> MemoryEntry:
        """写入一条记忆。

        写入前的检查（按顺序）：

        1. **凭据硬拦** —— 内容里有疑似密钥就拒绝。掩码后再存是一条没用的
           记忆，不如直接拒；
        2. **空内容** —— "好的"、"知道了"这种没有信息量的不记；
        3. **长度** —— 超长直接拒并说明上限；
        4. **冲突** —— 同主题的旧记忆与新内容矛盾时，必须 ``supersedes``
           指名覆盖，否则拒绝并列出冲突项。

        Args:
            content: 记忆正文（自包含）。
            kind: 记忆类型。
            tags: 检索标签（用用户会搜的词）。
            scope: ``session``（默认）或 ``long_term``。
            title: 一句话摘要；不给就截正文。
            supersedes: 被这条记忆覆盖的记忆 id。
            source: 来源（user / model-inferred）。

        Returns:
            写入后的记忆。

        Raises:
            CapabilityError: 校验没过，或这个 scope 没配置存储。
        """
        text = (content or "").strip()
        if not text:
            raise CapabilityError("记忆内容为空，没有可记的东西", code=ErrorCode.INVALID_ARGUMENTS)
        if contains_secret(text):
            raise CapabilityError(
                "内容里包含疑似凭据，已拒绝写入记忆",
                code=ErrorCode.INVALID_ARGUMENTS,
            )
        if len(text) > MAX_CONTENT_CHARS:
            raise CapabilityError(
                f"记忆内容过长（{len(text)} 字，上限 {MAX_CONTENT_CHARS} 字），请精简后再写",
                code=ErrorCode.INVALID_ARGUMENTS,
            )
        if kind not in MEMORY_KINDS:
            kind = KIND_NOTE

        store = self._store_for(scope)
        if store is None:
            raise CapabilityError(
                self._unavailable_message(scope),
                code=ErrorCode.EXECUTION_ERROR,
            )

        tag_list = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        digest = content_hash(text)

        # 重复：同内容直接合并（刷新强度与时间），不新增一条
        existing = await self._find_by_hash(store, digest)
        if existing is not None:
            merged = await store.add(
                MemoryEntry(
                    id=existing.id,
                    scope=scope,
                    title=existing.title,
                    content=text,
                    kind=kind,
                    tags=tag_list or existing.tags,
                    strength=max(existing.strength, _default_strength(kind)),
                    source=source,
                    source_thread_id=self.thread_id,
                )
            )
            refreshed = await store.get(merged)
            return refreshed or existing

        # 冲突：同主题（类型相同 + 有共同标签）但内容不同 —— 要求显式覆盖
        #
        # 这里**不直接覆盖**，而是让模型看到旧的那条说了什么，再决定要不要
        # 推翻它。直接覆盖有风险：同标签但其实两回事的记忆（"回答用中文" 与
        # "注释用英文"）会被误判，模型若顺手带上 supersedes 就把别的记忆删了。
        # 走两步（先拒绝、模型看清再带 supersedes 重试）之后，误判的代价是
        # 多一轮往返，附带好处是把标签逼得更精确。
        if not supersedes:
            conflicts = await self._find_conflicts(store, kind, tag_list, text)
            if conflicts:
                detail = "；".join(f"{item.id}：{item.summary(80)}" for item in conflicts)
                raise CapabilityError(
                    f"已有同主题的记忆（{detail}）。"
                    "如果这条要取代其中某一条，请带上 supersedes=<那条的 id> 重试；"
                    "如果是另一回事，请把标签写得更具体（少用共用的大标签）。",
                    code=ErrorCode.INVALID_ARGUMENTS,
                )

        item_id = await store.add(
            MemoryEntry(
                id="",
                scope=scope,
                title=(title or text)[:MAX_TITLE_CHARS],
                content=text,
                kind=kind,
                tags=tag_list,
                strength=_default_strength(kind),
                source=source,
                source_thread_id=self.thread_id,
            )
        )

        if supersedes:
            # 标记取代，不删除 —— 取代关系是模型判断的，它可能判断错。
            # 标记之后旧的那条不再参与召回，但还在库里能看见、能恢复。
            await store.supersede(supersedes)

        created = await store.get(item_id)
        if created is None:
            raise CapabilityError("记忆写入后读不回来", code=ErrorCode.EXECUTION_ERROR)
        return created

    async def forget(self, item_id: str) -> bool:
        """删除一条记忆。"""
        store = self._store_for(_scope_of(item_id))
        if store is None:
            return False
        return await store.forget(item_id)

    async def forget_thread(self, thread_id: str) -> int:
        """删掉一个会话的全部短期记忆。

        会话被删时调用 —— 短期记忆与对话历史**同生共死**。长期记忆不受影响：
        它是跨会话的，不属于某个会话。
        """
        if self._session_store is None:
            return 0
        return await self._session_store.forget_thread(thread_id)

    # ---------- 内部 ----------

    def _store_for(self, scope: str) -> MemoryStore | None:
        """按 scope 取存储；没有就返回 ``None``。"""
        if scope == SCOPE_LONG_TERM:
            return self.long_term_store
        return self._session_store

    def require_store(self, scope: str) -> MemoryStore:
        """按 scope 取存储；没有就报错。

        给能力用：查不到东西和"这个 scope 压根没配"是两回事，前者是正常结果，
        后者要明确告诉模型为什么。
        """
        store = self._store_for(scope)
        if store is None:
            raise CapabilityError(
                self._unavailable_message(scope),
                code=ErrorCode.EXECUTION_ERROR,
            )
        return store

    def _unavailable_message(self, scope: str) -> str:
        """没配存储时给一句可操作的提示（不静默失败）。"""
        if scope == SCOPE_LONG_TERM:
            return (
                "未配置长期记忆集合。请在 RAG 界面创建集合，"
                "并把集合名设置到环境变量 MEMORY_COLLECTION_NAME。"
            )
        return "当前没有可用的记忆存储（缺少数据库会话）。"

    async def _find_by_hash(self, store: MemoryStore, digest: str) -> MemoryEntry | None:
        """按内容哈希找已有记忆。"""
        for entry in await store.list_all(limit=500):
            if content_hash(entry.content) == digest:
                return entry
        return None

    async def _find_conflicts(
        self,
        store: MemoryStore,
        kind: str,
        tags: list[str],
        text: str,
    ) -> list[MemoryEntry]:
        """找同主题但内容不同的旧记忆。

        判据：**类型相同 + 至少有一个共同标签**。

        为什么不用"标签高度重合"：真正矛盾的两条，说的往往不是同一套词 ——
        "项目用 pnpm" 与 "项目改用 npm" 的正文重合度很低，只有标签能指出
        它们讲的是同一个话题（依赖管理）。反过来，两条都叫"偏好"但讲的
        是不同的事，那是正常的多条记忆。

        共同标签这个判据偏宽是**故意的**：误判的代价只是多一轮往返（模型
        看清旧的那条内容后可以改用更具体的标签重写），而漏判的代价是两条
        矛盾的记忆并存，召回时模型无从判断该信哪个。
        """
        if not tags:
            return []
        tag_set = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
        conflicts: list[MemoryEntry] = []
        for entry in await store.list_all(limit=500):
            if entry.kind != kind or not entry.tags:
                continue
            if entry.superseded:
                # 已被取代的（更早的那版）不算冲突 —— 它已经不参与召回了
                continue
            other = {str(tag).strip().lower() for tag in entry.tags if str(tag).strip()}
            if not (tag_set & other):
                continue
            if content_hash(entry.content) == content_hash(text):
                continue
            conflicts.append(entry)
        return conflicts[:5]


def _apply_scoring(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """给召回结果算综合分并排序。

    相关度主导，时间与使用次数只做微调 —— 这样"很久没用但依然相关"的记忆
    不会被一条"刚用过但不太相关"的记忆挤下去。
    """
    now = datetime.now(UTC)
    scored: list[MemoryEntry] = []
    for entry in entries:
        entry.relevance = effective_score(
            entry.relevance,
            entry.use_count,
            entry.last_used_at or entry.created_at,
            settings.memory_decay_half_life_days,
            now,
        )
        scored.append(entry)
    scored.sort(key=lambda item: item.relevance, reverse=True)
    return scored


def _scope_of(item_id: str) -> str:
    """从 id 前缀判断 scope。"""
    return SCOPE_LONG_TERM if str(item_id).startswith("l:") else SCOPE_SESSION


def _default_strength(kind: str) -> float:
    """不同类型的默认重要性。

    偏好与决定是用户明确表达的，最值钱；事实与画像次之；其它最低。
    """
    return {
        "preference": 0.8,
        "decision": 0.8,
        "convention": 0.7,
        "fact": 0.5,
        "profile": 0.6,
    }.get(kind, 0.4)


__all__ = ["MAX_CONTENT_CHARS", "MemoryService"]
