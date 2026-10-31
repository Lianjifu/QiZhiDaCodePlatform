"""调用记录补任务置信度

Revision ID: 0005_invocation_confidence
Revises: 0004_builtin_specs
Create Date: 2026-10-31

改动：
- invocation +confidence（本次调用之后的任务置信度，0-100）

置信度从调用记录算出来（格式类错误扣得轻、语义类错误扣得重、成功回补），
所以这个列只是把当时的分数存下来，方便回溯"这次调用时任务有多可信"。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_invocation_confidence"
down_revision: str | None = "0004_builtin_specs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invocation", sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invocation") as batch:
        batch.alter_column("confidence", existing_type=sa.Float(), nullable=True)
    op.drop_column("invocation", "confidence")
