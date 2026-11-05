"""RAG 语料检索能力：让 agent 能查平台的知识库。

平台有 RAG 层（集合 / 文档 / 分块），能入库、能检索 —— 但那些接口只给人用。
agent 查不到自己的知识库里有什么，于是要么不用，要么凭印象编。

两个能力，跟记忆那一对同构：

```text
rag_search  →  命中片段的标题 / 来源 / 摘要（不给整篇）
rag_read    →  一篇文档的全文
```

**只读、幂等、无副作用** —— 风险是 read，不触发任何审批。
出口是**固定的**（向量化端点由平台配好，模型只能决定发什么检索词），
所以按固定出口处理，不按 network 风险拦 —— 否则 balanced 模式下每查一次
知识库都要人工审批。

**为什么扫全部集合时要排除记忆集合**：长期记忆有自己的一套语义 ——
被取代的记忆不参与召回、模型写的东西按"模型说的"降权。走 RAG 原始检索会把
这些规则全绕过去，agent 会读到已经作废的记忆。记忆交给 ``memory_search``，
这里不重复（显式点名记忆集合时仍然可以搜）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.core.config import settings
from app.rag.models import RagCollection
from app.schemas.rag import RagSearchRequest

# 一次最多返回多少条。
#
# 20 条 × 每条 400 字摘要 ≈ 8000 字符 ≈ 两千多 token。一次给太多，模型会
# 在一堆不相干的片段里翻找，反而不如少给几条、看完再搜。
MAX_TOP_K = 20
DEFAULT_TOP_K = 5
# 每条命中给模型看的片段长度上限
SNIPPET_CHARS = 400


def make_rag_search(context: ExecutionContext) -> Callable[..., Any]:
    """构造"检索知识库"能力。"""

    async def rag_search(
        query: str,
        collection: str = "",
        top_k: int = DEFAULT_TOP_K,
    ) -> dict[str, Any]:
        """在知识库里检索。

        用来查"这件事以前是怎么做的""这个约定写在哪"这类问题 ——
        知识库里有答案时，**先查再答**，别凭印象编。

        Args:
            query: 检索词。用你真正想找的东西来搜，别用泛词。
            collection: 在哪个集合里找。留空 = 搜所有集合（记忆集合除外）。
            top_k: 最多返回几条，默认 5，最多 20。

        Returns:
            ``content`` 是编号后的命中片段（给模型看），``items`` 是结构化字段
            （集合名 / 文档 id / 标题 / 来源 / 分数）。

        Raises:
            CapabilityError: 检索词为空、top_k 越界、集合不存在、没有数据库会话。
        """
        query = (query or "").strip()
        if not query:
            raise CapabilityError("检索词不能为空", code=ErrorCode.INVALID_ARGUMENTS)
        if top_k < 1 or top_k > MAX_TOP_K:
            raise CapabilityError(
                f"top_k 只能是 1~{MAX_TOP_K} 之间的整数", code=ErrorCode.INVALID_ARGUMENTS
            )
        if context.session is None:
            raise CapabilityError(
                "检索知识库需要数据库会话（直连调用时不可用）。",
                code=ErrorCode.EXECUTION_ERROR,
            )

        async with context.db():
            from app.rag.service import RagService

            service = RagService(context.session)
            collections = await service.list_collections()
            targets = _select_collections(collections, collection)

            if not targets:
                return {
                    "content": "知识库里还没有可检索的集合。",
                    "query": query,
                    "collections": [],
                    "items": [],
                }

            merged: list[dict[str, Any]] = []
            degraded: list[str] = []
            for target in targets:
                result = await service.search(
                    RagSearchRequest(
                        collection_id=target.id,
                        query=query,
                        top_k=top_k,
                        mode="hybrid",
                    )
                )
                if result.degraded and target.embedding_model:
                    # **只报"配了向量化却没跑成"的降级** —— 集合压根没配
                    # embedding 模型（纯关键词）是用户的配置选择，不是故障，
                    # 报成"降级"会让人以为哪里坏了。
                    degraded.append(f"{target.name}：{result.degraded}")
                merged.extend(_hits_of(result, target))

        merged.sort(key=lambda item: item["score"], reverse=True)
        items = merged[:top_k]

        lines: list[str] = []
        for index, item in enumerate(items, start=1):
            lines.append(
                f"{index}. [{item['collection']}] {item['title'] or '（无标题）'}"
                f" | 来源: {item['source'] or '无'} | 分数: {item['score']:.3f}"
            )
            lines.append(f"   {item['snippet']}")
        if items:
            lines.append("要整篇正文就调用 rag_read(document_id)。")
        else:
            lines.append(f"知识库里没找到与 {query!r} 相关的片段。")
        note = ("（部分集合退化为关键词检索：{}）".format("；".join(degraded))) if degraded else ""

        return {
            "content": "\n".join([note, *lines] if note else lines),
            "query": query,
            "collections": [item.name for item in targets],
            "items": items,
        }

    return rag_search


def make_rag_read(context: ExecutionContext) -> Callable[..., Any]:
    """构造"读知识库文档"能力。"""

    async def rag_read(document_id: int) -> dict[str, Any]:
        """取一篇文档的完整正文。

        Args:
            document_id: 文档 id（``rag_search`` 给的）。

        Returns:
            ``content`` 是正文（给模型看），其余是结构化字段。

        Raises:
            CapabilityError: 文档不存在、没有数据库会话。
        """
        if context.session is None:
            raise CapabilityError(
                "读取知识库文档需要数据库会话（直连调用时不可用）。",
                code=ErrorCode.EXECUTION_ERROR,
            )

        try:
            document_id = int(document_id)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(
                "document_id 必须是整数", code=ErrorCode.INVALID_ARGUMENTS
            ) from exc

        async with context.db():
            from app.core.exceptions import NotFoundError
            from app.rag.service import RagService

            service = RagService(context.session)
            try:
                document = await service.get_document(document_id)
            except NotFoundError as exc:
                raise CapabilityError(
                    f"文档 {document_id} 不存在", code=ErrorCode.INVALID_ARGUMENTS
                ) from exc
            collection = await service.get_collection(document.collection_id)

        return {
            "content": document.content,
            "document_id": document.id,
            "collection": collection.name,
            "title": document.title,
            "source": document.source,
            "format": document.format,
            "meta": dict(document.meta or {}),
        }

    return rag_read


def _select_collections(
    collections: list[RagCollection], name: str
) -> list[RagCollection]:
    """挑要搜哪些集合。

    点名了就只搜那一个；没点名就搜全部，**但排除记忆集合** ——
    记忆有自己的一套召回语义（被取代的不召回），走原始检索会把那些规则绕过去。
    """
    wanted = (name or "").strip()
    if wanted:
        for collection in collections:
            if collection.name == wanted:
                return [collection]
        available = "、".join(c.name for c in collections) or "（一个都没有）"
        raise CapabilityError(
            f"没有叫 {wanted!r} 的集合。可用的集合：{available}",
            code=ErrorCode.INVALID_ARGUMENTS,
        )
    memory_name = settings.memory_collection_name.strip()
    return [c for c in collections if c.name != memory_name]


def _hits_of(result: Any, collection: RagCollection) -> list[dict[str, Any]]:
    """把一次检索的命中整理成统一的条目。

    **按文档去重**：一篇文档可能被多个分块命中（长文档会切块）——
    同一篇文档只出现一次，取分数最高的那块。
    """
    best: dict[int, dict[str, Any]] = {}
    for hit in result.hits:
        document_id = hit.chunk.document_id
        current = best.get(document_id)
        if current is not None and hit.score <= current["score"]:
            continue
        best[document_id] = {
            "collection": collection.name,
            "document_id": document_id,
            "title": hit.title or "",
            "source": hit.source or "",
            "score": round(float(hit.score), 4),
            "snippet": (hit.chunk.content or "")[:SNIPPET_CHARS],
        }
    return list(best.values())


__all__ = [
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "SNIPPET_CHARS",
    "make_rag_read",
    "make_rag_search",
]
