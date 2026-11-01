"""SQL 实现：SQLite 与 Postgres 同一套代码。

关键词检索用 **BM25**（而不是简单的词频计数）—— 长文档里出现 10 次的常见词
不该比出现 1 次的专有名词得分高。分词对中文用**字符二元组**（"记忆检索" →
"记忆""忆检""检索"），对英文按词切，并把 ``foo_bar`` 拆成 ``foo`` 和 ``bar``
—— 代码检索里命中其中一个也有意义。

向量检索在进程内算余弦。分块量到几万条之前这没问题（毫秒级），
再往上就把这一层换成 pgvector / 专用向量库，接口不变。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.embedding import cosine
from app.rag.models import RagChunk
from app.rag.vector_store import VectorStore

# 拉丁词（保留数字与下划线）
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# 连续中文
_CJK_RUN_RE = re.compile("[㐀-䶿一-鿿]+")
# 驼峰 / 蛇形分词
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")

# BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75
# 整串命中（函数名、环境变量名这类精确匹配）的加分
_EXACT_BONUS = 3.0


def tokenize(text: str) -> list[str]:
    """分词。

    英文按词、中文按二元组、标识符额外拆开。三种粒度混在一起，
    是因为检索场景本来就跨这三类：自然语言问题、函数名、环境变量名。
    """
    if not text:
        return []
    lowered = text.lower()
    tokens: list[str] = []
    seen: set[str] = set()

    for match in _WORD_RE.finditer(lowered):
        word = match.group(0)
        tokens.append(word)
        seen.add(word)
        if "_" in word:
            # foo_bar → foo, bar
            for part in word.split("_"):
                if part and part not in seen:
                    tokens.append(part)
                    seen.add(part)

    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            if run not in seen:
                tokens.append(run)
                seen.add(run)
            continue
        for index in range(len(run) - 1):
            piece = run[index : index + 2]
            if piece not in seen:
                tokens.append(piece)
                seen.add(piece)

    for match in _CAMEL_RE.finditer(text):
        piece = match.group(0).lower()
        if piece and piece not in seen:
            tokens.append(piece)
            seen.add(piece)

    return tokens


class SqlVectorStore(VectorStore):
    """SQL 实现的向量库。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- 写入 ----------

    async def add_chunks(self, chunks: list[RagChunk]) -> None:
        if not chunks:
            return
        self.session.add_all(chunks)
        await self.session.flush()

    async def delete_document(self, document_id: int) -> int:
        stmt = delete(RagChunk).where(RagChunk.document_id == document_id)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def delete_collection(self, collection_id: int) -> int:
        stmt = delete(RagChunk).where(RagChunk.collection_id == collection_id)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def load_chunks(self, collection_id: int) -> list[RagChunk]:
        stmt = (
            select(RagChunk)
            .where(RagChunk.collection_id == collection_id)
            .order_by(RagChunk.document_id, RagChunk.seq)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---------- 检索 ----------

    async def keyword_search(
        self, collection_id: int, query: str, limit: int = 50
    ) -> list[tuple[RagChunk, float]]:
        chunks = await self.load_chunks(collection_id)
        return keyword_rank(chunks, query, limit=limit)

    async def vector_search(
        self, collection_id: int, vector: list[float], limit: int = 50
    ) -> list[tuple[RagChunk, float]]:
        chunks = await self.load_chunks(collection_id)
        scored: list[tuple[RagChunk, float]] = []
        for chunk in chunks:
            embedding = chunk.embedding
            if not embedding:
                continue
            score = cosine(vector, list(embedding))
            if score <= 0:
                continue
            scored.append((chunk, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


def keyword_rank(
    chunks: list[RagChunk], query: str, *, limit: int = 50
) -> list[tuple[RagChunk, float]]:
    """BM25 关键词打分。

    打分之外还给"整串命中"加分：查 ``DATABASE_URL`` 时，
    文档里原样出现这个串，比只出现 ``URL`` 相关词更可信。
    """
    if not chunks or not query.strip():
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    stats = [_document_stats(chunk) for chunk in chunks]
    total = len(stats)
    avg_len = sum(item.length for item in stats) / total if total else 0.0

    doc_freq: Counter[str] = Counter()
    for item in stats:
        doc_freq.update(item.counts.keys())

    needle = query.strip().lower()
    scored: list[tuple[RagChunk, float]] = []
    for chunk, item in zip(chunks, stats, strict=True):
        if not item.length:
            continue
        score = 0.0
        for term in set(query_tokens):
            freq = item.counts.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (total - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * item.length / (avg_len or 1))
            score += idf * (freq * (_BM25_K1 + 1)) / denom
        if needle and needle in chunk.content.lower():
            score += _EXACT_BONUS
        if score > 0:
            scored.append((chunk, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


@dataclass
class _DocStats:
    """一块的词频统计。"""

    content: str
    counts: Counter
    length: int


# 分词结果缓存：分块内容不变就没必要重新分词。
# 检索是"扫全库"的活，不缓存的话每次查询都要把全库重新分一遍词。
_TOKEN_CACHE: dict[Any, _DocStats] = {}
_TOKEN_CACHE_MAX = 20000


def _document_stats(chunk: Any) -> _DocStats:
    """取（并缓存）一块的词频统计。"""
    pk = getattr(chunk, "id", None)
    key = pk if pk is not None else ("raw", getattr(chunk, "content", ""))
    content = getattr(chunk, "content", "") or ""

    cached = _TOKEN_CACHE.get(key)
    if cached is not None and cached.content == content:
        return cached

    tokens = tokenize(content)
    stats = _DocStats(content=content, counts=Counter(tokens), length=len(tokens))
    if len(_TOKEN_CACHE) >= _TOKEN_CACHE_MAX:
        _TOKEN_CACHE.clear()
    _TOKEN_CACHE[key] = stats
    return stats


__all__ = ["SqlVectorStore", "keyword_rank", "tokenize"]
