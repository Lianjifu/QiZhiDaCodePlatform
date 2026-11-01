"""存储实现。

只有 SQL 一种实现（SQLite / Postgres 同一套代码）。要换 pgvector 或专用
向量库时，在这里加一个实现 ``app.rag.vector_store.VectorStore`` 即可，
上层不用动。
"""

from __future__ import annotations

from app.rag.stores.sql import SqlVectorStore, keyword_rank, tokenize

__all__ = ["SqlVectorStore", "keyword_rank", "tokenize"]
