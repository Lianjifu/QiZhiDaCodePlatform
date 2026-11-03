"""审批改按风险档位判：低档自动放行，中高档才要人批

Revision ID: 0012_approval_by_risk_tier
Revises: 0011_llm_provider_vision
Create Date: 2026-11-04

改动：
- 出厂能力的 ``permission`` 统一改成 ``none`` —— 审批由**风险档位**决定，
  声明不再重复表达一遍；
- ``git_worktree_remove`` / ``git_merge`` 的风险改成 ``destructive``（高档）；
- ``delegate_worker`` 的风险改成 ``execute``（中档）。

**为什么需要它**：``sync_builtins`` 只补空字段、不覆盖已有行（那是刻意的 ——
运营改过的策略不该被启动流程冲掉）。所以出厂声明改了风险/权限之后，库里
的旧行不会自己跟上，得显式迁一次。

**为什么这样改**（用户定的口径：只有中高风险才需要审批）：

- 低档（只读、写入）→ 自动放行：改错了能改回来，不打断人；
- 中档（执行命令、联网、派子 agent）→ 要人批；
- 高档（破坏性：删文件、删 worktree、合并分支）→ 永远要人批，
  ``permissive`` 信任模式也不放过 —— 不可逆的东西不能自动放行。

用户自建的能力（``origin='user'``）一概不动 —— 那是运营自己配的策略。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_approval_by_risk_tier"
down_revision: str | None = "0011_llm_provider_vision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 风险改成高档（破坏性）：不可逆，永远要人批
_DESTRUCTIVE_TOOLS = ("git_worktree_remove", "git_merge")

# 风险改成中档（执行）：worker 会在自己的 worktree 里跑一整轮 agent
_EXECUTE_TOOLS = ("delegate_worker",)


def upgrade() -> None:
    conn = op.get_bind()
    # 出厂能力的权限要求统一交给风险档位判
    conn.execute(
        sa.text("UPDATE tool SET permission = 'none' WHERE origin = 'builtin'")
    )
    for name in _DESTRUCTIVE_TOOLS:
        conn.execute(
            sa.text("UPDATE tool SET risk = 'destructive' WHERE name = :name").bindparams(
                name=name
            )
        )
    for name in _EXECUTE_TOOLS:
        conn.execute(
            sa.text("UPDATE tool SET risk = 'execute' WHERE name = :name").bindparams(
                name=name
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for name in _DESTRUCTIVE_TOOLS:
        conn.execute(
            sa.text("UPDATE tool SET risk = 'write' WHERE name = :name").bindparams(
                name=name
            )
        )
    for name in _EXECUTE_TOOLS:
        conn.execute(
            sa.text("UPDATE tool SET risk = 'write' WHERE name = :name").bindparams(
                name=name
            )
        )
    conn.execute(
        sa.text("UPDATE tool SET permission = 'confirm' WHERE origin = 'builtin'")
    )
