"""检索：混合召回 + 融合。

**为什么必须混合**：向量检索擅长"意思相近"，关键词检索擅长"字面精确"。
只上向量的话，查 ``DATABASE_URL`` 这种环境变量名、查 ``parse_config`` 这种
函数名，经常召不回 —— 向量把下划线、驼峰这些细节抹平了。反过来只上关键词，
问"怎么配置数据库"就召不回写着 ``DATABASE_URL=postgres://...`` 的那一段。

两路结果用 **RRF（倒数排名融合）** 合并：

    score(d) = Σ 权重 / (k + rank(d))

用排名而不是分数来融合，是因为两路的分数不可比（余弦在 0-1，BM25 无上界），
归一化各归一化、各有各的毛病；排名是稳的。k 取 60 是原论文的经验值，
作用是让"排在第 1"和"排在第 10"的差距不至于太夸张。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import AppError
from app.rag.embedding import EmbeddingProvider
from app.rag.vector_store import ChunkHit, VectorStore

logger = logging.getLogger(__name__)

SEARCH_HYBRID = "hybrid"
SEARCH_VECTOR = "vector"
SEARCH_KEYWORD = "keyword"
SEARCH_MODES = (SEARCH_HYBRID, SEARCH_VECTOR, SEARCH_KEYWORD)

# RRF 的平滑常数
_RRF_K = 60


@dataclass
class SearchRequest:
    """一次检索请求。"""

    query: str
    collection_id: int
    top_k: int = 8
    mode: str = SEARCH_HYBRID
    # 归一化后的最低分，低于它直接丢掉
    min_score: float = 0.0
    # 结构化过滤：分块 meta 的子集匹配
    filters: dict[str, Any] = field(default_factory=dict)
    # 每路先取多少条再融合
    candidate_size: int = 50


@dataclass
class SearchResult:
    """一次检索的结果。"""

    hits: list[ChunkHit] = field(default_factory=list)
    mode: str = SEARCH_HYBRID
    took_ms: int = 0
    total: int = 0
    # 降级说明：比如集合没启用向量化，实际只走了关键词
    degraded: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [hit.to_dict() for hit in self.hits],
            "mode": self.mode,
            "took_ms": self.took_ms,
            "total": self.total,
            "degraded": self.degraded,
        }


# 重排钩子：拿到 (query, hits) 返回重排后的 hits
Reranker = Callable[[str, list[ChunkHit]], Awaitable[list[ChunkHit]]]


class Retriever:
    """检索器。

    ``embedder`` 为 None 或集合没启用向量化时，自动退化成纯关键词检索 ——
    不会因为没配向量服务就整个不可用。
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider | None = None,
        *,
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
        reranker: Reranker | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.reranker = reranker

    async def search(self, request: SearchRequest) -> SearchResult:
        started = time.perf_counter()
        query = request.query.strip()
        if not query:
            return SearchResult(mode=request.mode, took_ms=0, total=0)

        mode = request.mode if request.mode in SEARCH_MODES else SEARCH_HYBRID
        degraded = ""

        vector_ranking: list[tuple[Any, float]] = []
        keyword_ranking: list[tuple[Any, float]] = []
        vector_detail = ""

        if mode in (SEARCH_HYBRID, SEARCH_KEYWORD):
            keyword_ranking = await self.store.keyword_search(
                request.collection_id, query, limit=request.candidate_size
            )
        if mode in (SEARCH_HYBRID, SEARCH_VECTOR):
            vector_ranking, vector_detail = await self._vector_search(request, query)

        if mode == SEARCH_VECTOR and not vector_ranking:
            # 没有向量可用（集合没启用向量化，或模型没配出来）→ 真的退回关键词
            degraded = f"向量化检索不可用（{vector_detail or '没有向量'}），已退化为纯关键词检索"
            if not keyword_ranking:
                keyword_ranking = await self.store.keyword_search(
                    request.collection_id, query, limit=request.candidate_size
                )
            mode = SEARCH_KEYWORD
        elif mode == SEARCH_HYBRID and not vector_ranking:
            degraded = f"向量化检索没有结果（{vector_detail or '没有向量'}），已退化为纯关键词检索"

        fused = self._fuse(vector_ranking, keyword_ranking, mode=mode)

        # 结构化过滤放在融合之后：过滤掉的结果不占坑位
        if request.filters:
            fused = [
                item
                for item in fused
                if _meta_matches(getattr(item[0], "meta", None), request.filters)
            ]

        hits = self._to_hits(fused)
        if request.min_score > 0:
            hits = [hit for hit in hits if hit.score >= request.min_score]
        total = len(hits)
        hits = hits[: max(1, request.top_k)]

        if self.reranker is not None and hits:
            hits = await self.reranker(query, hits)
            for index, hit in enumerate(hits):
                hit.rank = index + 1

        return SearchResult(
            hits=hits,
            mode=mode,
            took_ms=int((time.perf_counter() - started) * 1000),
            total=total,
            degraded=degraded,
        )

    async def _vector_search(
        self, request: SearchRequest, query: str
    ) -> tuple[list[tuple[Any, float]], str]:
        """走向量检索。

        向量化这一步失败（模型没配、端点挂了、密钥过期）不该让整个检索挂掉 ——
        关键词那一路还能用。失败原因带回去，写在 ``degraded`` 里。
        """
        if self.embedder is None:
            return [], "集合未启用向量化"
        try:
            vectors = await self.embedder.embed([query])
        except AppError as exc:
            logger.warning("向量化检索失败，退化为关键词检索：%s", exc.message)
            return [], f"向量化失败：{exc.message}"
        if not vectors or not vectors[0]:
            return [], "向量化没有返回向量"
        ranking = await self.store.vector_search(
            request.collection_id, list(vectors[0]), limit=request.candidate_size
        )
        return ranking, ""

    def _fuse(
        self,
        vector_ranking: list[tuple[Any, float]],
        keyword_ranking: list[tuple[Any, float]],
        *,
        mode: str,
    ) -> list[tuple[Any, float, float | None, float | None]]:
        """融合两路排名，返回 ``(分块, 融合分, 向量分, 关键词分)``。

        ``融合分`` 一律归一到 0-1（对外承诺的分数范围）；
        ``向量分`` / ``关键词分`` 保留原始值，方便调效果时看得见是哪一路捞的。
        """
        if mode == SEARCH_VECTOR:
            # 余弦本身就在 0-1，直接用
            return [(item, score, score, None) for item, score in vector_ranking]
        if mode == SEARCH_KEYWORD:
            # BM25 没有上界，按最高分归一
            top = max((score for _, score in keyword_ranking), default=0.0)
            return [
                (item, (score / top) if top > 0 else 0.0, None, score)
                for item, score in keyword_ranking
            ]

        scores: dict[Any, float] = {}
        vectors: dict[Any, float] = {}
        keywords: dict[Any, float] = {}
        chunks: dict[Any, Any] = {}

        for rank, (chunk, score) in enumerate(vector_ranking, start=1):
            key = _chunk_key(chunk)
            chunks[key] = chunk
            vectors[key] = score
            scores[key] = scores.get(key, 0.0) + self.vector_weight / (_RRF_K + rank)

        for rank, (chunk, score) in enumerate(keyword_ranking, start=1):
            key = _chunk_key(chunk)
            chunks[key] = chunk
            keywords[key] = score
            scores[key] = scores.get(key, 0.0) + self.keyword_weight / (_RRF_K + rank)

        # 归一化到 0-1：两种路都满分的理论上限是两者之和
        ceiling = (self.vector_weight + self.keyword_weight) / (_RRF_K + 1)
        fused = [
            (
                chunks[key],
                (score / ceiling) if ceiling > 0 else 0.0,
                vectors.get(key),
                keywords.get(key),
            )
            for key, score in scores.items()
        ]
        fused.sort(key=lambda item: item[1], reverse=True)
        return fused

    @staticmethod
    def _to_hits(fused: list[tuple[Any, float, float | None, float | None]]) -> list[ChunkHit]:
        hits: list[ChunkHit] = []
        for index, (chunk, score, vector_score, keyword_score) in enumerate(fused):
            hits.append(
                ChunkHit(
                    chunk=chunk,
                    score=score,
                    vector_score=vector_score,
                    keyword_score=keyword_score,
                    rank=index + 1,
                )
            )
        return hits


def _chunk_key(chunk: Any) -> Any:
    """分块的稳定标识。

    两路检索各自查了一遍库，拿到的 ORM 实例不是同一个对象 ——
    只能靠主键把它们对上。用 ``id()`` 会合不起来。
    """
    pk = getattr(chunk, "id", None)
    if pk is not None:
        return pk
    return id(chunk)


def _meta_matches(meta: dict[str, Any] | None, filters: dict[str, Any]) -> bool:
    """meta 的子集匹配：filters 里每个键都要在 meta 里有相等的值。"""
    if not filters:
        return True
    if not meta:
        return False
    for key, expected in filters.items():
        if key not in meta:
            return False
        if meta[key] != expected:
            return False
    return True


__all__ = [
    "SEARCH_HYBRID",
    "SEARCH_KEYWORD",
    "SEARCH_MODES",
    "SEARCH_VECTOR",
    "Reranker",
    "Retriever",
    "SearchRequest",
    "SearchResult",
]
