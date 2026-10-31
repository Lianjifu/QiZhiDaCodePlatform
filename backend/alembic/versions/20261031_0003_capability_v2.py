"""能力层 v2：声明式契约 + 调用记录生命周期

Revision ID: 0003_capability_v2
Revises: 0002_capability_layer
Create Date: 2026-10-31

改动：
- tool         +risk +output_schema +timeout_s +retry_policy +annotations
- tool         permission 取值域迁移：low→none、high→confirm
- execution_log 改名 invocation，并扩成"一次调用的完整生命周期"：
  +payload +intent_fingerprint +idempotency_key +status +risk +permission
  +decision_reason +stages +result +decided_at +started_at +finished_at
  去掉 ok（由 status 表达）

新增的 NOT NULL 列都走「先可空 → 回填 → 改回非空」，已有数据不会写不进去。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_capability_v2"
down_revision: str | None = "0002_capability_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------- tool：声明字段 ----------
    op.add_column("tool", sa.Column("risk", sa.String(length=16), nullable=True))
    op.add_column("tool", sa.Column("output_schema", sa.JSON(), nullable=True))
    op.add_column("tool", sa.Column("timeout_s", sa.Integer(), nullable=True))
    op.add_column("tool", sa.Column("retry_policy", sa.JSON(), nullable=True))
    op.add_column("tool", sa.Column("annotations", sa.JSON(), nullable=True))
    op.execute("UPDATE tool SET risk = 'read' WHERE risk IS NULL")
    op.execute("UPDATE tool SET timeout_s = 30 WHERE timeout_s IS NULL")
    op.execute("UPDATE tool SET retry_policy = '{}' WHERE retry_policy IS NULL")
    op.execute("UPDATE tool SET annotations = '{}' WHERE annotations IS NULL")
    with op.batch_alter_table("tool") as batch:
        batch.alter_column("risk", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("timeout_s", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("retry_policy", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("annotations", existing_type=sa.JSON(), nullable=False)

    # ---------- tool：权限取值域迁移 ----------
    # 老的 low/high 表达的是"风险高低"，新值域里风险由 risk 单独表达，
    # permission 只表示"谁能放行"：low → none，high → confirm。
    op.execute("UPDATE tool SET permission = 'none' WHERE permission = 'low'")
    op.execute("UPDATE tool SET permission = 'confirm' WHERE permission = 'high'")

    # ---------- execution_log → invocation ----------
    # 索引先删掉：SQLite 改名表不会跟着改索引名，PostgreSQL 也不会
    op.drop_index("ix_execution_log_thread_id", table_name="execution_log")
    op.drop_index("ix_execution_log_capability_key", table_name="execution_log")
    op.rename_table("execution_log", "invocation")

    op.add_column("invocation", sa.Column("payload", sa.JSON(), nullable=True))
    op.add_column(
        "invocation", sa.Column("intent_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column("invocation", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("invocation", sa.Column("status", sa.String(length=16), nullable=True))
    op.add_column("invocation", sa.Column("risk", sa.String(length=16), nullable=True))
    op.add_column("invocation", sa.Column("permission", sa.String(length=16), nullable=True))
    op.add_column("invocation", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column("invocation", sa.Column("stages", sa.JSON(), nullable=True))
    op.add_column("invocation", sa.Column("result", sa.JSON(), nullable=True))
    op.add_column("invocation", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invocation", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invocation", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE invocation SET payload = '{}' WHERE payload IS NULL")
    op.execute("UPDATE invocation SET intent_fingerprint = '' WHERE intent_fingerprint IS NULL")
    op.execute("UPDATE invocation SET status = 'succeeded' WHERE status IS NULL")
    op.execute("UPDATE invocation SET risk = 'read' WHERE risk IS NULL")
    op.execute("UPDATE invocation SET permission = 'none' WHERE permission IS NULL")
    op.execute("UPDATE invocation SET decision_reason = '' WHERE decision_reason IS NULL")
    op.execute("UPDATE invocation SET stages = '[]' WHERE stages IS NULL")
    op.execute("UPDATE invocation SET result = '{}' WHERE result IS NULL")
    # 老记录只有 ok：true → succeeded，false → failed
    op.execute("UPDATE invocation SET status = CASE WHEN ok THEN 'succeeded' ELSE 'failed' END")
    op.execute("UPDATE invocation SET finished_at = created_at WHERE finished_at IS NULL")

    with op.batch_alter_table("invocation") as batch:
        batch.alter_column("payload", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("intent_fingerprint", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("status", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("risk", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("permission", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("decision_reason", existing_type=sa.Text(), nullable=False)
        batch.alter_column("stages", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("result", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("ok")

    op.create_index("ix_invocation_thread_id", "invocation", ["thread_id"])
    op.create_index("ix_invocation_capability_key", "invocation", ["capability_key"])
    op.create_index("ix_invocation_status", "invocation", ["status"])
    op.create_index("ix_invocation_idempotency_key", "invocation", ["idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_invocation_idempotency_key", table_name="invocation")
    op.drop_index("ix_invocation_status", table_name="invocation")
    op.drop_index("ix_invocation_capability_key", table_name="invocation")
    op.drop_index("ix_invocation_thread_id", table_name="invocation")

    # 先把 status 折回 ok —— 顺序不能反，status 列马上就要被删掉
    op.add_column("invocation", sa.Column("ok", sa.Boolean(), nullable=True))
    op.execute("UPDATE invocation SET ok = 1 WHERE status = 'succeeded'")
    op.execute("UPDATE invocation SET ok = 0 WHERE ok IS NULL")

    with op.batch_alter_table("invocation") as batch:
        batch.alter_column("ok", existing_type=sa.Boolean(), nullable=False)
        batch.drop_column("payload")
        batch.drop_column("intent_fingerprint")
        batch.drop_column("idempotency_key")
        batch.drop_column("status")
        batch.drop_column("risk")
        batch.drop_column("permission")
        batch.drop_column("decision_reason")
        batch.drop_column("stages")
        batch.drop_column("result")
        batch.drop_column("decided_at")
        batch.drop_column("started_at")
        batch.drop_column("finished_at")

    op.rename_table("invocation", "execution_log")
    op.create_index("ix_execution_log_thread_id", "execution_log", ["thread_id"])
    op.create_index("ix_execution_log_capability_key", "execution_log", ["capability_key"])

    op.execute("UPDATE tool SET permission = 'low' WHERE permission = 'none'")
    op.execute("UPDATE tool SET permission = 'high' WHERE permission = 'confirm'")

    with op.batch_alter_table("tool") as batch:
        batch.alter_column("annotations", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("retry_policy", existing_type=sa.JSON(), nullable=True)
        batch.alter_column("timeout_s", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("risk", existing_type=sa.String(length=16), nullable=True)
    op.drop_column("tool", "annotations")
    op.drop_column("tool", "retry_policy")
    op.drop_column("tool", "timeout_s")
    op.drop_column("tool", "risk")
    op.drop_column("tool", "output_schema")
