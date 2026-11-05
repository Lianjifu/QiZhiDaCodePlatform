"""自我进化闭环：复盘记录表 + 提示词规则表

Revision ID: 0014_retrospective
Revises: 0013_memory_content_hash
Create Date: 2026-11-05

改动：
- 新增 ``retrospective``（复盘记录：一次「执行结果 → 改进」的账）
- 新增 ``prompt_rule``（自动积累的提示词规则）
- ``invocation`` 加一列 ``retrospective_id``（这条失败被哪次复盘覆盖过）

**为什么要有这三样。**

平台早就把每次调用的完整轨迹写进了 ``invocation``，但那张表只有人在管理界面
看得见 —— agent 自己读不到，所以它会在同一轮里反复试同一个失败的做法。

现在补一条闭环：每轮结束、这一轮有失败时，平台跑一次复盘，把失败摊开给模型看，
让它答「错在哪、下次怎么改、这条改进属于哪一层」，然后落进记忆 / 技能 / 提示词
三层之一。

**复盘是免审批的** —— 平台不让人先看一眼再改。那**事后**就得能查：
``retrospective`` 记下改了什么、为什么改、成功没有、能不能撤。

**``invocation.retrospective_id`` 是去重 + 度量的依据**：
没复盘过的失败（``retrospective_id IS NULL``）才该复盘；
「这条失败在改进之后有没有又发生」是衡量改进有没有用的唯一依据。

**不加外键**：``retrospective`` 与 ``invocation`` 互相引用会成环，删表顺序就
定不下来。这里只存 id，删复盘时把它置空即可。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_retrospective"
down_revision: str | None = "0013_memory_content_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrospective",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("task_summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reject_reason", sa.String(length=32), nullable=True),
        sa.Column("carrier", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("target_skill_id", sa.Integer(), nullable=True),
        sa.Column("appended_block", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("capability_keys", sa.JSON(), nullable=False),
        sa.Column("invocation_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrospective_thread_id", "retrospective", ["thread_id"])
    op.create_index("ix_retrospective_status", "retrospective", ["status"])

    op.create_table(
        "prompt_rule",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("retrospective_id", sa.Integer(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_rule_retrospective_id", "prompt_rule", ["retrospective_id"])

    with op.batch_alter_table("invocation") as batch:
        batch.add_column(
            sa.Column("retrospective_id", sa.Integer(), nullable=True),
        )
    op.create_index("ix_invocation_retrospective_id", "invocation", ["retrospective_id"])


def downgrade() -> None:
    op.drop_index("ix_invocation_retrospective_id", table_name="invocation")
    with op.batch_alter_table("invocation") as batch:
        batch.drop_column("retrospective_id")

    op.drop_index("ix_prompt_rule_retrospective_id", table_name="prompt_rule")
    op.drop_table("prompt_rule")

    op.drop_index("ix_retrospective_status", table_name="retrospective")
    op.drop_index("ix_retrospective_thread_id", table_name="retrospective")
    op.drop_table("retrospective")
