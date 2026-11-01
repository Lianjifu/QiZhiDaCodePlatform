"""调用记录补「已回流给 agent」标记

Revision ID: 0007_agent_capability_flow
Revises: 0006_rag_layer
Create Date: 2026-11-01

改动：
- invocation +notified_at

审批是挂起的：agent 那一轮在「等待审批」处就结束了，人工批准后审批接口
把那次调用执行掉了 —— 但 agent 的上下文里没有这个结果。notified_at 用来
标记「这条结果已经送回 agent 了」，保证只送一次。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_agent_capability_flow"
down_revision: str | None = "0006_rag_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invocation", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invocation") as batch:
        batch.drop_column("notified_at")
