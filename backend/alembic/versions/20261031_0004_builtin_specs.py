"""内置能力出厂规格升级

Revision ID: 0004_builtin_specs
Revises: 0003_capability_v2
Create Date: 2026-10-31

两件事：

1. **内置能力的模块路径变了** —— ``app.capabilities.builtin.*`` 挪到了
   ``app.capabilities.registration.builtin.*``（注册层下面）。存量库里存的是
   老路径，不改的话运行时会 import 失败。
2. **出厂规格升级** —— 补上 risk / timeout_s / retry_policy / annotations /
   output_schema，并把入参 schema 换成带约束的新版。

这些字段是**平台声明**（不是运营自由填的内容），所以这里直接覆盖；
用户自建的能力（origin=user）不受影响。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_builtin_specs"
down_revision: str | None = "0003_capability_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# key -> (risk, permission, timeout_s, retry_policy, annotations)
_BUILTIN_DECLARATIONS: dict[str, tuple[str, str, int, dict, dict]] = {
    "read_file": ("read", "none", 10, {}, {"read_only": True, "idempotent": True}),
    "write_file": ("write", "none", 10, {}, {"idempotent": True}),
    "edit_file": ("write", "none", 10, {}, {"idempotent": True}),
    "move_file": ("write", "none", 10, {}, {}),
    "copy_file": ("write", "none", 10, {}, {"idempotent": True}),
    "make_dir": ("write", "none", 10, {}, {"idempotent": True}),
    "change_directory": ("read", "none", 5, {}, {"read_only": True, "idempotent": True}),
    "list_files": ("read", "none", 10, {}, {"read_only": True, "idempotent": True}),
    "glob": ("read", "none", 15, {}, {"read_only": True, "idempotent": True}),
    "grep": ("read", "none", 20, {}, {"read_only": True, "idempotent": True}),
    "run_python": (
        "execute",
        "confirm",
        60,
        {"max_attempts": 2, "backoff_s": 0.5, "retry_on": ["TIMEOUT"]},
        {"destructive": True, "open_world": True},
    ),
    "run_bash": (
        "execute",
        "confirm",
        60,
        {"max_attempts": 2, "backoff_s": 0.5, "retry_on": ["TIMEOUT"]},
        {"destructive": True, "open_world": True},
    ),
    "web_fetch": (
        "network",
        "confirm",
        20,
        {"max_attempts": 3, "backoff_s": 1.0, "retry_on": ["NETWORK_ERROR"]},
        {"open_world": True},
    ),
    "web_search": (
        "network",
        "confirm",
        20,
        {"max_attempts": 3, "backoff_s": 1.0, "retry_on": ["NETWORK_ERROR"]},
        {"open_world": True},
    ),
}

_OLD_SOURCE_PREFIX = "app.capabilities.builtin."
_NEW_SOURCE_PREFIX = "app.capabilities.registration.builtin."


def upgrade() -> None:
    # ---------- 模块路径 ----------
    op.execute(
        sa.text(
            "UPDATE tool SET source = :new || substr(source, :old_len + 1) "
            "WHERE source LIKE :old_prefix || '%'"
        ).bindparams(
            new=_NEW_SOURCE_PREFIX,
            old_len=len(_OLD_SOURCE_PREFIX),
            old_prefix=_OLD_SOURCE_PREFIX,
        )
    )

    # ---------- 出厂规格 ----------
    connection = op.get_bind()
    for key, (risk, permission, timeout_s, retry_policy, hints) in _BUILTIN_DECLARATIONS.items():
        connection.execute(
            sa.text(
                "UPDATE tool SET risk = :risk, permission = :permission, "
                "timeout_s = :timeout_s, retry_policy = :retry, annotations = :annotations "
                "WHERE name = :name AND origin = 'builtin'"
            ).bindparams(
                risk=risk,
                permission=permission,
                timeout_s=timeout_s,
                retry=json.dumps(retry_policy),
                annotations=json.dumps(hints),
                name=key,
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tool SET source = :old || substr(source, :new_len + 1) "
            "WHERE source LIKE :new_prefix || '%'"
        ).bindparams(
            old=_OLD_SOURCE_PREFIX,
            new_len=len(_NEW_SOURCE_PREFIX),
            new_prefix=_NEW_SOURCE_PREFIX,
        )
    )
    # 规格字段回滚交给 0003 的 downgrade（它会删掉这些列）
