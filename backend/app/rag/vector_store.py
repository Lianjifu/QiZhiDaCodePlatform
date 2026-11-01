"""向量存储的抽象。

把"存在哪儿"和"怎么检索"分开，是为了让检索策略（混合、融合、重排）不依赖
具体的存储实现。

v1 是 SQL 实现（SQLite / Postgres 同一套代码）：向量存在 JSON 列里，
检索在进程内算余弦。几千条分块毫秒级，**不引入任何新依赖**。

规模上去了有两条路，都在这一层后面：

- 换存储：Postgres 换 pgvector（asyncpg 已经是依赖了），实现同一个协议即可；
- 换索引：关键词检索换成 SQLite FTS5 / Postgres tsvector，接口不变。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.rag.models import RagChunk


@dataclass
class ChunkHit:
    """一条检索结果。

    保留向量分与关键词分两个来源，不只是合成后的总分 ——
    调检索效果时得看得见"是哪一路把它捞出来的"。
    """

    chunk: RagChunk
    score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    rank: int = 0
    # 来自所属文档的信息，由服务层回填
    title: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "seq": self.chunk.seq,
            "content": self.chunk.content,
            "score": round(self.score, 4),
            "vector_score": _round_or_none(self.vector_score),
            "keyword_score": _round_or_none(self.keyword_score),
            "rank": self.rank,
            "title": self.title,
            "source": self.source,
            "meta": dict(self.chunk.meta or {}),
        }


def _round_or_none(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


class VectorStore(ABC):
    """向量 + 关键词的存取与检索。"""

    @abstractmethod
    async def add_chunks(self, chunks: list[RagChunk]) -> None:
        """写入分块（含向量）。"""

    @abstractmethod
    async def delete_document(self, document_id: int) -> int:
        """删掉一篇文档的全部分块，返回删除条数。"""

    @abstractmethod
    async def delete_collection(self, collection_id: int) -> int:
        """删掉一个集合的全部分块，返回删除条数。"""

    @abstractmethod
    async def load_chunks(self, collection_id: int) -> list[RagChunk]:
        """取出一个集合的全部分块（内存里算相似度用）。"""

    @abstractmethod
    async def keyword_search(
        self, collection_id: int, query: str, limit: int = 50
    ) -> list[tuple[RagChunk, float]]:
        """关键词检索，返回 ``(分块, 分数)``，按分数降序。"""

    @abstractmethod
    async def vector_search(
        self, collection_id: int, vector: list[float], limit: int = 50
    ) -> list[tuple[RagChunk, float]]:
        """向量检索，返回 ``(分块, 余弦相似度)``，按分数降序。"""


__all__ = ["ChunkHit", "VectorStore"]
