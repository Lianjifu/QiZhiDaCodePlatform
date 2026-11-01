"""删掉 invocation.notified_at —— 结果回流机制没了

Revision ID: 0008_drop_notified_at
Revises: 0007_agent_capability_flow
Create Date: 2026-11-01

改动：
- invocation -notified_at

审批改成 LangGraph 中断：批准后**图恢复**，agent 自己接着往下跑，结果直接回到
它的上下文。不再需要"下一轮把结果补给它"那套回流机制，于是 notified_at 这个
"已送回 agent"的标记就没有存在意义了。

被 deepAgents 接管的出厂内置能力（read_file / write_file / run_bash …）的
下线不在这里做 —— 那份名单在代码里（`registration/tool_policy.py`），
由 `registry.sync_builtins()` 在每次启动时按名单下线，数据库与代码不会漂移。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_drop_notified_at"
down_revision: str | None = "0007_agent_capability_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("invocation") as batch:
        batch.drop_column("notified_at")


def downgrade() -> None:
    op.add_column("invocation", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
