"""供应商配置：加视觉能力列

Revision ID: 0011_llm_provider_vision
Revises: 0010_llm_provider_context_window
Create Date: 2026-11-02

改动：
- llm_provider 加 vision_enabled（布尔）

**为什么需要它**：对话里上传的图片是**直接内联进消息**发给模型的
（``{"type": "image", ...}`` → 供应商侧转成 ``image_url``）。模型不认这个
格式就会直接报错。DeepSeek 实测能读图（能说出图里左右分别是红蓝），但
Ollama / vLLM 这类自建端点、以及同厂商的纯文本模型都不一定行。

所以内联之前必须知道"这个模型能不能看图"。不能看图时降级成只把文件路径
告诉 agent（它可以用 execute 里的 Python 去处理），而不是让整轮对话报错。

存量记录按**厂商预设**回填：OpenAI / Anthropic / DeepSeek / Gemini /
Azure OpenAI 的默认模型都能看图，其余保守留 False —— 猜"能"会让对话直接
报错，猜"不能"只是少一条能力，代价小得多。用户可以在配置里随时改。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_llm_provider_vision"
down_revision: str | None = "0010_llm_provider_context_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("llm_provider") as batch:
        batch.add_column(
            sa.Column(
                "vision_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                comment="模型能不能看图",
            )
        )

    from app.agents.providers import PROVIDER_PRESETS

    capable = [preset.key for preset in PROVIDER_PRESETS if preset.vision_enabled]
    if capable:
        conn = op.get_bind()
        for provider_key in capable:
            conn.execute(
                sa.text(
                    "UPDATE llm_provider SET vision_enabled = 1 "
                    "WHERE provider = :provider"
                ).bindparams(provider=provider_key)
            )


def downgrade() -> None:
    with op.batch_alter_table("llm_provider") as batch:
        batch.drop_column("vision_enabled")
