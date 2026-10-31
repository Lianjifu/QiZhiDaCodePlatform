"""能力层重构：注册/编排/执行分层 + 能力清单统一

Revision ID: 0002_capability_layer
Revises: 0001_baseline
Create Date: 2026-10-31

改动：
- tool         +origin +tags +permission +version
- skill        prompt 改名 knowledge，+checklist +examples +constraints +tags +version
- mcp_server   +tags +version
- 新增 execution_log（执行轨迹）

新增的 NOT NULL 列都走「先可空 → 回填 → 改回非空」，
这样已有数据不会因为没有默认值而写不进去。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_capability_layer"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------- tool ----------
    op.add_column("tool", sa.Column("origin", sa.String(length=16), nullable=True))
    op.add_column("tool", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("tool", sa.Column("permission", sa.String(length=16), nullable=True))
    op.add_column("tool", sa.Column("version", sa.Integer(), nullable=True))
    op.execute("UPDATE tool SET origin = 'user' WHERE origin IS NULL")
    op.execute("UPDATE tool SET tags = '[]' WHERE tags IS NULL")
    op.execute("UPDATE tool SET permission = 'low' WHERE permission IS NULL")
    op.execute("UPDATE tool SET version = 1 WHERE version IS NULL")
    with op.batch_alter_table("tool") as batch:
        batch.alter_column("origin", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("permission", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)

    # ---------- skill ----------
    with op.batch_alter_table("skill") as batch:
        batch.alter_column("prompt", new_column_name="knowledge", existing_type=sa.Text())
    op.add_column("skill", sa.Column("checklist", sa.JSON(), nullable=True))
    op.add_column("skill", sa.Column("examples", sa.JSON(), nullable=True))
    op.add_column("skill", sa.Column("constraints", sa.Text(), nullable=True))
    op.add_column("skill", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("skill", sa.Column("version", sa.Integer(), nullable=True))
    op.execute("UPDATE skill SET checklist = '[]' WHERE checklist IS NULL")
    op.execute("UPDATE skill SET examples = '[]' WHERE examples IS NULL")
    op.execute("UPDATE skill SET constraints = '' WHERE constraints IS NULL")
    op.execute("UPDATE skill SET tags = '[]' WHERE tags IS NULL")
    op.execute("UPDATE skill SET version = 1 WHERE version IS NULL")
    with op.batch_alter_table("skill") as batch:
        batch.alter_column("checklist", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("examples", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("constraints", existing_type=sa.Text(), nullable=False)
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)

    # ---------- mcp_server ----------
    op.add_column("mcp_server", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("mcp_server", sa.Column("version", sa.Integer(), nullable=True))
    op.execute("UPDATE mcp_server SET tags = '[]' WHERE tags IS NULL")
    op.execute("UPDATE mcp_server SET version = 1 WHERE version IS NULL")
    with op.batch_alter_table("mcp_server") as batch:
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("version", existing_type=sa.Integer(), nullable=False)

    # ---------- execution_log ----------
    op.create_table(
        "execution_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("capability_key", sa.String(length=64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_log_capability_key", "execution_log", ["capability_key"])
    op.create_index("ix_execution_log_thread_id", "execution_log", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_execution_log_thread_id", table_name="execution_log")
    op.drop_index("ix_execution_log_capability_key", table_name="execution_log")
    op.drop_table("execution_log")

    with op.batch_alter_table("mcp_server") as batch:
        batch.alter_column("version", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=True)
    op.drop_column("mcp_server", "version")
    op.drop_column("mcp_server", "tags")

    with op.batch_alter_table("skill") as batch:
        batch.alter_column("version", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("constraints", existing_type=sa.Text(), nullable=True)
        batch.alter_column("examples", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("checklist", existing_type=sa.JSON(), nullable=True)
    op.drop_column("skill", "version")
    op.drop_column("skill", "tags")
    op.drop_column("skill", "constraints")
    op.drop_column("skill", "examples")
    op.drop_column("skill", "checklist")
    with op.batch_alter_table("skill") as batch:
        batch.alter_column("knowledge", new_column_name="prompt", existing_type=sa.Text())

    with op.batch_alter_table("tool") as batch:
        batch.alter_column("version", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("permission", existing_type=sa.String(length=16), nullable=True)
        batch.alter_column("tags", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("origin", existing_type=sa.String(length=16), nullable=True)
    op.drop_column("tool", "version")
    op.drop_column("tool", "permission")
    op.drop_column("tool", "tags")
    op.drop_column("tool", "origin")
