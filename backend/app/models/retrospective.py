"""复盘记录：一次「执行结果 → 改进」的账。

## 这张表记什么

agent 每跑完一轮，若这一轮有失败，平台会跑一次复盘：把失败摊开给模型看，
让它答「错在哪、下次怎么改、这条改进属于哪一层」，然后落进记忆 / 技能 / 提示词
三层之一。

这一行就是那次复盘的**账**：

- **改了什么** —— ``carrier`` + ``title`` / ``content`` + ``target_id``；
- **为什么改** —— ``reason``（模型给的理由）；
- **改成功没有** —— ``status`` 与 ``reject_reason``；
- **依据是哪些失败** —— 覆盖的调用记录行会把自己的 ``retrospective_id``
  指回这里（见 ``app/models/invocation.py``）。

## 为什么要有这张表

**免审批。** 平台不让人先看一眼再改，那**事后**就得能查：改了什么、谁触发的、
能不能撤。三层都能撤销，撤销的依据也在这张表里。

没有这张表的话，"agent 自动改了自己的提示词"这件事就是查无实据 ——
出了事没人说得清它改了什么、为什么改。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 复盘的状态
STATUS_RUNNING = "running"  # 正在跑（已抢占失败记录）
STATUS_APPLIED = "applied"  # 改进已落地
STATUS_NO_INSIGHT = "no_insight"  # 复盘了，但没发现值得改的
STATUS_REJECTED = "rejected"  # 有改进，但没落地（看 reject_reason）
STATUS_FAILED = "failed"  # 复盘本身没跑成

# 被拒的原因
REJECT_SAFETY_SCOPE = "safety_scope"  # 越界：规则想改审批 / 权限那一层
REJECT_CONFLICT = "conflict"  # 与已有记忆矛盾
REJECT_LIMIT = "limit"  # 载体装不下了（技能 knowledge 有长度上限）
REJECT_TARGET_MISSING = "target_missing"  # 要改的目标没了
REJECT_PARSE_FAILED = "parse_failed"  # 模型没给出可解析的结果

# 改进落在哪一层
CARRIER_MEMORY = "memory"
CARRIER_SKILL = "skill"
CARRIER_PROMPT = "prompt"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Retrospective(Base):
    """一次复盘的记录。"""

    __tablename__ = "retrospective"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 来源会话。跨会话的复盘（周期沉淀）还没有，所以可以为空。
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # 这次任务是什么（用户消息，截断）—— 复盘要回答"错在哪"就得知道"想做什么"
    task_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_RUNNING, index=True, nullable=False
    )
    # 被拒的具体原因（status=rejected 时有值）
    reject_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 改进落在哪一层
    carrier: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 记忆的分类（只有 carrier=memory 时有意义）
    kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 落地后的目标：记忆 id（s:12 / l:3）、技能 id、提示词规则 id
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    target_skill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 追加进技能 knowledge 的那段原文 —— 撤销时按它删回去
    appended_block: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 模型给的理由（为什么这样改）
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # 这次改进针对哪些能力 —— 算"同类失败复发率"用
    capability_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # 覆盖了几条失败
    invocation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "CARRIER_MEMORY",
    "CARRIER_PROMPT",
    "CARRIER_SKILL",
    "REJECT_CONFLICT",
    "REJECT_LIMIT",
    "REJECT_PARSE_FAILED",
    "REJECT_SAFETY_SCOPE",
    "REJECT_TARGET_MISSING",
    "STATUS_APPLIED",
    "STATUS_FAILED",
    "STATUS_NO_INSIGHT",
    "STATUS_REJECTED",
    "STATUS_RUNNING",
    "Retrospective",
]
