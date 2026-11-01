"""供应商配置：加上下文窗口列

Revision ID: 0010_llm_provider_context_window
Revises: 0009_memory_item
Create Date: 2026-11-01

改动：
- llm_provider 加 context_window（整数，0 = 未知）

**为什么需要它**：deepAgents 的摘要中间件按
``model.profile["max_input_tokens"]`` 算触发阈值与保留比例，而 OpenAI 兼容端点
挂上去的模型对象没有 profile —— 不显式告诉框架窗口多大，它只能退回到固定的
"170000 token 触发、保留 6 条消息"。对 1M 窗口的模型来说，那等于只用了不到
两成窗口就开始压缩，压完只剩 6 条消息。

存量记录按**厂商预设**回填：DeepSeek 1M、OpenAI 128k 这样的大窗口，
不填的话压缩阈值会退回到"170000 token 触发、保留 6 条"，对它们来说是错的。

厂商预设里没有的（自定义端点、本地模型）留 0（未知）—— 拿不到就宁可让框架用
保守默认值，也不要瞎猜一个大窗口：猜大了会在真正的窗口用满前就开始压缩，
猜小了会压缩得太晚、直接撞上厂商的报错。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_llm_provider_context_window"
down_revision: str | None = "0009_memory_item"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_provider") as batch:
        batch.add_column(
            sa.Column(
                "context_window",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="上下文窗口（token），0=未知",
            )
        )

    # 按厂商预设回填存量记录。预设表是应用代码，迁移里直接引用 ——
    # 迁移与应用跑在同一个代码库里，不存在版本漂移的问题。
    from app.agents.providers import PROVIDER_PRESETS

    windows = {preset.key: preset.context_window for preset in PROVIDER_PRESETS}
    windows = {key: value for key, value in windows.items() if value > 0}
    if windows:
        conn = op.get_bind()
        for provider_key, window in windows.items():
            conn.execute(
                sa.text(
                    "UPDATE llm_provider SET context_window = :window "
                    "WHERE provider = :provider AND context_window = 0"
                ).bindparams(window=window, provider=provider_key)
            )


def downgrade() -> None:
    with op.batch_alter_table("llm_provider") as batch:
        batch.drop_column("context_window")
