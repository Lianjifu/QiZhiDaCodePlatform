"""RAG 服务层：集合 / 文档 / 分块

Revision ID: 0006_rag_layer
Revises: 0005_invocation_confidence
Create Date: 2026-11-01

改动：
- 新增 rag_collection（集合：绑定向量化配置，锁死模型与维度）
- 新增 rag_document（文档：原文 + 内容哈希，用于去重）
- 新增 rag_chunk（分块：检索单元，带向量）
- llm_provider +embedding_model +embedding_dim（复用现有模型接入配置做向量化）

集合上记死 embedding_model 与 embedding_dim：不同模型的向量不在同一个空间里，
混着比出来的相似度是噪声。换模型要重建集合，不是改字段。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_rag_layer"
down_revision: str | None = "0005_invocation_confidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts_col(name: str) -> sa.Column:
    """时间戳列（建表和回写用同一套默认值）。"""
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    # ---------- llm_provider：向量化模型 ----------
    op.add_column(
        "llm_provider", sa.Column("embedding_model", sa.String(length=128), nullable=True)
    )
    op.add_column("llm_provider", sa.Column("embedding_dim", sa.Integer(), nullable=True))
    op.execute("UPDATE llm_provider SET embedding_model = '' WHERE embedding_model IS NULL")
    op.execute("UPDATE llm_provider SET embedding_dim = 0 WHERE embedding_dim IS NULL")
    with op.batch_alter_table("llm_provider") as batch:
        batch.alter_column("embedding_model", existing_type=sa.String(length=128), nullable=False)
        batch.alter_column("embedding_dim", existing_type=sa.Integer(), nullable=False)

    # ---------- rag_collection ----------
    op.create_table(
        "rag_collection",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        _ts_col("created_at"),
        _ts_col("updated_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_collection_name", "rag_collection", ["name"], unique=True)

    # ---------- rag_document ----------
    op.create_table(
        "rag_document",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        _ts_col("created_at"),
        _ts_col("updated_at"),
        sa.ForeignKeyConstraint(["collection_id"], ["rag_collection.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_document_collection_id", "rag_document", ["collection_id"])
    op.create_index("ix_rag_document_content_hash", "rag_document", ["content_hash"])

    # ---------- rag_chunk ----------
    op.create_table(
        "rag_chunk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        _ts_col("created_at"),
        sa.ForeignKeyConstraint(["collection_id"], ["rag_collection.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["rag_document.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_chunk_collection_id", "rag_chunk", ["collection_id"])
    op.create_index("ix_rag_chunk_document_id", "rag_chunk", ["document_id"])
    op.create_index(
        "ix_rag_chunk_collection_seq", "rag_chunk", ["collection_id", "seq"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_rag_chunk_collection_seq", table_name="rag_chunk")
    op.drop_index("ix_rag_chunk_document_id", table_name="rag_chunk")
    op.drop_index("ix_rag_chunk_collection_id", table_name="rag_chunk")
    op.drop_table("rag_chunk")

    op.drop_index("ix_rag_document_content_hash", table_name="rag_document")
    op.drop_index("ix_rag_document_collection_id", table_name="rag_document")
    op.drop_table("rag_document")

    op.drop_index("ix_rag_collection_name", table_name="rag_collection")
    op.drop_table("rag_collection")

    with op.batch_alter_table("llm_provider") as batch:
        batch.drop_column("embedding_dim")
        batch.drop_column("embedding_model")
