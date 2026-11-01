"""RAG 层的 ORM 模型：集合 / 文档 / 分块。

三层结构，一一对应检索链路上的三步：

```text
collection（一个语料集合，绑定一套向量化配置）
  └─ document（一篇原始文档，按内容哈希去重）
       └─ chunk（检索单元，带向量）
```

**集合必须绑定固定的向量化配置**：不同模型算出的向量维度不同、空间也不同，
混在一起检索等于乱比。所以 `embedding_model` / `embedding_dim` 记在集合上，
换模型 = 重建集合，不是改个字段。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 集合可见范围
SCOPE_PRIVATE = "private"
SCOPE_SHARED = "shared"


def utcnow() -> datetime:
    return datetime.now(UTC)


class RagCollection(Base):
    """一个语料集合（检索的边界）。"""

    __tablename__ = "rag_collection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # private（只有创建者可见）| shared（团队共享）
    scope: Mapped[str] = mapped_column(String(16), default=SCOPE_PRIVATE, nullable=False)

    # ---- 向量化配置 ----
    # 模型名（空 = 不启用向量化，只走关键词检索）
    embedding_model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 向量维度。探测出来的，写库前就知道，不会出现半库半向量的情况
    embedding_dim: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 用哪条供应商配置发请求（存 llm_provider.id）
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- 分块参数 ----
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=150, nullable=False)

    # 冗余计数，方便列表页展示（改文档时同步维护）
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class RagDocument(Base):
    """一篇原始文档。"""

    __tablename__ = "rag_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rag_collection.id"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # 来源：文件路径 / URL / 人工录入
    source: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # text | markdown | code
    format: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    # 原文（分块前的完整内容）
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 内容哈希：同一集合里内容一样就认为是同一篇，避免重复入库
    content_hash: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class RagChunk(Base):
    """一个检索单元。"""

    __tablename__ = "rag_chunk"
    __table_args__ = (Index("ix_rag_chunk_collection_seq", "collection_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rag_collection.id"), index=True, nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rag_document.id"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 向量（float 列表）。没启用向量化时是 None
    embedding: Mapped[list[float] | None] = mapped_column(JSON, default=None, nullable=True)
    # 该向量实际维度，用来发现"混了不同模型的向量"这种事故
    dim: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


__all__ = [
    "SCOPE_PRIVATE",
    "SCOPE_SHARED",
    "RagChunk",
    "RagCollection",
    "RagDocument",
]
