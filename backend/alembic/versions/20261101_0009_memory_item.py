"""记忆模块：会话内记忆表

Revision ID: 0009_memory_item
Revises: 0008_drop_notified_at
Create Date: 2026-11-01

改动：
- 新增 memory_item（会话内记忆：随 thread 存活，删会话才清）

跨会话的长期记忆**不在这张表** —— 它落在 RAG 的专用集合里。两种存储的检索与
更新方式完全不同，硬塞进一张表会让两边都别扭。

长期记忆的召回频率会很高（每轮都要查），所以这里给 thread_id 建了索引。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_memory_item"
down_revision: str | None = "0008_drop_notified_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_item_thread_id", "memory_item", ["thread_id"])
    op.create_index("ix_memory_item_kind", "memory_item", ["kind"])
    op.create_index("ix_memory_item_content_hash", "memory_item", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_memory_item_content_hash", table_name="memory_item")
    op.drop_index("ix_memory_item_kind", table_name="memory_item")
    op.drop_index("ix_memory_item_thread_id", table_name="memory_item")
    op.drop_table("memory_item")
