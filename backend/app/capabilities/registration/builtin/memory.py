"""记忆类内置能力：召回 / 读取 / 写入。

对应三层渐进式披露：

```text
memory_search  →  命中条目的**摘要** + id + 分数（不给全文）
memory_read    →  一条记忆的全文
memory_write   →  写入或合并一条记忆
```

**为什么 search 不返回全文**：一次搜索可能命中 20 条，全文全给就是几千 token。
给摘要（每条 200 字以内）+ id，模型挑中哪条再 ``memory_read`` 取全文 ——
上下文里始终只有"目录级"的信息量。

**为什么这三个能力都要走执行层**：记忆是持久化状态，写入会改变系统状态。走
七阶段流水线意味着有风险判定、有审批、有留痕 —— 与"模型只能通过工具产生
副作用"这条硬约定一致。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.memory.models import (
    MEMORY_KINDS,
    SCOPE_ALL,
    SCOPE_LONG_TERM,
    SCOPE_SESSION,
    SOURCE_MODEL,
)
from app.memory.service import MemoryService

# scope 的取值 —— 会话内、跨会话，或两个都搜
_SCOPES = (SCOPE_SESSION, SCOPE_LONG_TERM, SCOPE_ALL)


def _service(context: ExecutionContext) -> MemoryService:
    """按执行上下文造一个记忆服务。

    长期记忆的存储要连 RAG 集合，所以只在**真的要查长期记忆时**才建 ——
    没配集合名时，建它就会抛错，而会话内记忆不该被这个连累。
    """
    long_term = None
    if context.session is not None and _long_term_configured():
        from app.memory.long_term_store import LongTermMemoryStore

        long_term = LongTermMemoryStore(context.session)
    return MemoryService(
        session=context.session,
        thread_id=context.thread_id,
        long_term_store=long_term,
        db_lock=context.db_lock,
    )


def _long_term_configured() -> bool:
    """长期记忆集合配了没有。"""
    from app.core.config import settings

    return bool(settings.memory_collection_name.strip())


def _resolve_scope(context: ExecutionContext, scope: str) -> tuple[str, str]:
    """实际用哪个 scope，以及要不要跟模型说一声。

    **降级而不是报错**：长期记忆集合没配时，模型写 ``long_term`` 会直接失败 ——
    而模型失败之后往往不会重试，而是自己找个别的办法记（改写工作区的
    AGENTS.md）。那比记在会话里更糟：既不受预算控制，也不做相关性排序。

    所以这里把长期降级成会话内，并把降级的事实回给模型，让它能转告用户。
    """
    if scope == SCOPE_LONG_TERM and not _long_term_configured():
        return SCOPE_SESSION, "（长期记忆集合未配置，这条已改记在会话内）"
    return scope, ""


def _require_session_scope(context: ExecutionContext) -> None:
    """会话内记忆必须有会话上下文。

    没有 thread_id 时，所有直连调用会共用一个空桶 —— 那不是"会话内记忆"，
    是全局记忆。直接说清楚。
    """
    if not context.thread_id:
        raise CapabilityError(
            "会话内记忆需要在会话中使用（缺少会话上下文）。跨会话的记忆请把 scope 设成 long_term。",
            code=ErrorCode.INVALID_ARGUMENTS,
        )


def make_memory_search(context: ExecutionContext) -> Callable[..., Any]:
    """构造"召回记忆"能力。"""

    async def memory_search(
        query: str,
        scope: str = SCOPE_ALL,
        top_k: int = 8,
    ) -> dict[str, Any]:
        """召回记忆，返回命中条目的摘要。

        Args:
            query: 检索词。用你真正想找的东西来搜，别用泛词。
            scope: ``all``（会话内 + 跨会话，默认）、``session``（会话内）
                或 ``long_term``（跨会话）。
            top_k: 最多返回几条，默认 8。

        Returns:
            ``content`` 是编号后的摘要列表（给模型看），``items`` 是结构化字段
            （id / 类型 / 标签 / 分数 / scope）。

        Raises:
            CapabilityError: scope 不合法、会话内记忆缺上下文、长期记忆没配集合。
        """
        if scope not in _SCOPES:
            raise CapabilityError(f"scope 只能是 {_SCOPES} 之一", code=ErrorCode.INVALID_ARGUMENTS)
        scope, note = _resolve_scope(context, scope)
        if scope == SCOPE_SESSION:
            _require_session_scope(context)
        service = _service(context)
        if scope != SCOPE_ALL:
            service.require_store(scope)

        entries = await service.recall(query, scope=scope, top_k=top_k)
        if not entries:
            return {
                "content": f"没有找到与 {query!r} 相关的{_scope_label(scope)}记忆",
                "query": query,
                "scope": scope,
                "items": [],
            }
        lines: list[str] = []
        items: list[dict[str, Any]] = []
        for index, entry in enumerate(entries, start=1):
            lines.append(
                f"{index}. [{entry.id}] {entry.kind} | {entry.summary()}"
                f" | 标签: {', '.join(entry.tags) if entry.tags else '无'}"
            )
            items.append(
                {
                    "id": entry.id,
                    "kind": entry.kind,
                    "scope": entry.scope,
                    "tags": list(entry.tags),
                    "summary": entry.summary(),
                    "score": round(entry.relevance, 4),
                    "use_count": entry.use_count,
                }
            )
        lines.append("需要正文就调用 memory_read(id)。")
        return {
            "content": "\n".join([note, *lines] if note else lines),
            "query": query,
            "scope": scope,
            "items": items,
        }

    return memory_search


def make_memory_read(context: ExecutionContext) -> Callable[..., Any]:
    """构造"读取记忆正文"能力。"""

    async def memory_read(id: str) -> dict[str, Any]:
        """取一条记忆的完整正文。

        Args:
            id: 记忆 id（``memory_search`` 给的）。

        Returns:
            ``content`` 是正文（给模型看），其余是结构化字段。

        Raises:
            CapabilityError: id 不存在。
        """
        service = _service(context)
        entry = await service.read(id)
        if entry is None:
            raise CapabilityError(f"记忆 {id} 不存在", code=ErrorCode.INVALID_ARGUMENTS)
        return {
            "content": entry.content,
            "id": entry.id,
            "scope": entry.scope,
            "kind": entry.kind,
            "tags": list(entry.tags),
            "created_at": entry.created_at.isoformat() if entry.created_at else "",
            "source": entry.source,
        }

    return memory_read


def make_memory_write(context: ExecutionContext) -> Callable[..., Any]:
    """构造"写入记忆"能力。"""

    async def memory_write(
        content: str,
        kind: str = "note",
        tags: list[str] | None = None,
        scope: str = SCOPE_SESSION,
        title: str = "",
        supersedes: str = "",
    ) -> dict[str, Any]:
        """写入一条记忆。

        **只记值得记的**：未来某个请求里会用到的、用户明确说过的、且不能从
        工作区里直接读出来的内容。临时的请求参数、模型自己的推断、从代码里
        就能查到的事实，都不该记。

        Args:
            content: 记忆正文。要**自包含**（脱离上下文也能读懂），并带上将来
                能检索到的关键词（项目名、技术名）。一条只讲一件事。
            kind: 记忆类型（preference / decision / convention / fact / profile /
                note）。选对了以后召回才准。
            tags: 检索标签。用**用户会搜的词**（"pnpm"、"前端"），别用内部
                分类词（"pref"、"tech"）。
            scope: ``session``（会话内，默认）或 ``long_term``（跨会话）。
                默认会话内 —— 长期记忆是持久的，写入要更明确的动作。
            title: 一句话摘要；不给就截正文。
            supersedes: 被这条覆盖的记忆 id。**用户改主意时必须指明**，
                否则会跟旧的那条打架。

        Returns:
            ``content`` 是给模型看的结果说明，``id`` 是新记忆的 id。

        Raises:
            CapabilityError: 内容为空、含疑似凭据、超长、类型不合法、
                与已有记忆主题冲突（需要 supersedes）、长期记忆没配集合。
        """
        if scope not in _SCOPES:
            raise CapabilityError(f"scope 只能是 {_SCOPES} 之一", code=ErrorCode.INVALID_ARGUMENTS)
        if kind not in MEMORY_KINDS:
            raise CapabilityError(
                f"kind 只能是 {MEMORY_KINDS} 之一", code=ErrorCode.INVALID_ARGUMENTS
            )
        scope, note = _resolve_scope(context, scope)
        if scope == SCOPE_SESSION:
            _require_session_scope(context)

        service = _service(context)
        entry = await service.remember(
            content=content,
            kind=kind,
            tags=list(tags or []),
            scope=scope,
            title=title,
            supersedes=supersedes or None,
            source=SOURCE_MODEL,
        )
        return {
            "content": (
                f"已记下（{entry.id}，{entry.kind}）：{entry.summary()}{note}"
                if scope == SCOPE_SESSION
                else f"已记入长期记忆（{entry.id}，{entry.kind}）：{entry.summary()}{note}"
            ),
            "id": entry.id,
            "scope": entry.scope,
            "kind": entry.kind,
            "tags": list(entry.tags),
        }

    return memory_write


def _scope_label(scope: str) -> str:
    if scope == SCOPE_ALL:
        return ""
    return "会话内" if scope == SCOPE_SESSION else "长期"


__all__ = [
    "make_memory_read",
    "make_memory_search",
    "make_memory_write",
]
