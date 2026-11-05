"""反思：让 agent 看得见"自己刚才卡在哪"。

## 为什么是这一块

自主反思的第一道门槛不是"能不能复盘"，而是"**知不知道自己失败过**"。

平台把每次调用的完整轨迹都写进了 ``invocation`` 表 —— 哪个能力、什么参数、
评估出的风险、哪一步失败、错误是什么，全有。**但 agent 读不到它**：
没有工具暴露这张表，也没有一块上下文告诉它"上次 run_python 因为 xxx 失败了"。

于是模型会在同一轮里反复试同一个失败的做法 —— 它不是不长记，
是**没有记可读**。

## 两条路径

| | 什么时候 | 给多少 |
| --- | --- | --- |
| **自动注入** | 每轮开始 | 最近几次失败的一行摘要（占位符替换） |
| **按需工具** | 模型觉得需要 | 错误码、完整报错、参数摘要 |

自动注入走上下文占位符（每轮换内容，不进图）；工具走能力层
（有审批、有留痕）。两条路径查的是同一张表、用同一个判定 ——
注入里看到什么，工具里就查得到什么。

## 什么算"失败" —— 两层

**第一层：记录的状态**。``failed`` / ``blocked`` / ``denied`` / ``expired``。

**第二层：工具跑成了，但要做的事没做成** —— 退出码非零。

第二层是**代码 agent 最常见的失败形态**，而且它在记录里是 ``succeeded``：
``run_python`` 的职责是"在沙箱里跑一段代码并把退出码与输出带回来"，
代码抛异常时这个职责**履行了** —— 所以记录是成功的。

但从 agent 的角度，那次尝试就是失败了：它想算的东西没算出来。
只看状态的话，反思块永远是空的 —— 恰恰在最需要它的场景里空着。

**为什么不改记录的状态**：状态还喂给置信度机制（失败会扣分、低于阈值
就阻塞任务）。把"测试跑挂了"算成能力失败，会让"写完代码跑一遍"这条
工作准则每次都把任务扣到阻塞线 —— 那是把记录改坏，不是改准。

所以这里只改**读取时的判断**，不动写入时的语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invocation import (
    FAILURE_STATUSES,
    STATUS_BLOCKED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_FAILED,
)

# 自动注入时最多列几条 —— 再多就不是"提醒"而是"历史"，模型会当成上下文噪声。
MAX_INJECTED = 5

# 往前看多少条调用记录来找失败。
#
# **这是个窗口，不是条数上限** —— 窗口里的失败最多报 ``MAX_INJECTED`` 条。
# 窗口的作用是把陈旧的失败挤出去：后面又跑了几十次调用，早先那次失败
# 就不在窗口里了。这样不用引入"多久算过期"这种拍脑袋的时间阈值。
SCAN_WINDOW = 20

# 单条错误信息在注入块里截到多长。完整报错留给工具。
MAX_MESSAGE_CHARS = 120

# 状态 → 人话。给模型看的，别把枚举名甩给它。
_STATUS_LABELS = {
    STATUS_FAILED: "执行失败",
    STATUS_BLOCKED: "被阻塞",
    STATUS_DENIED: "被拒绝",
    STATUS_EXPIRED: "审批过期",
}


@dataclass
class FailureNote:
    """一次没成功的调用。"""

    capability_key: str
    status: str
    error_code: str = ""
    error_message: str = ""
    created_at: datetime | None = None
    # 退出码非零 —— 工具本身跑成了，但要做的事没做成
    exit_code: int | None = None

    @property
    def status_label(self) -> str:
        """状态的人话。"""
        if self.exit_code is not None and self.exit_code != 0:
            return f"退出码 {self.exit_code}"
        return _STATUS_LABELS.get(self.status, self.status)

    def summary(self, *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
        """一行摘要 —— 自动注入用这个。"""
        text = f"{self.capability_key}（{self.status_label}）"
        if self.error_code:
            text = f"{text} {self.error_code}"
        message = (self.error_message or "").strip().replace("\n", " ")
        if message:
            if len(message) > max_chars:
                message = message[:max_chars] + "…"
            text = f"{text}：{message}"
        return text

    def render(self) -> str:
        """完整一点的一行 —— 工具返回用这个。"""
        parts = [f"- {self.capability_key}（{self.status_label}）"]
        if self.error_code:
            parts.append(f"错误码 {self.error_code}")
        if self.created_at:
            parts.append(self.created_at.strftime("%m-%d %H:%M"))
        message = (self.error_message or "").strip()
        if message:
            parts.append(message)
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """结构化字段（工具返回的 ``items``）。"""
        return {
            "capability_key": self.capability_key,
            "status": self.status,
            "status_label": self.status_label,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "exit_code": self.exit_code,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


def is_failure(record: Any) -> bool:
    """这条调用记录算不算"没成功"。

    两层判断（见模块文档）：记录的状态，或者是退出码非零。

    Args:
        record: ``Invocation`` 行（或任何有 ``status`` 与 ``result`` 属性的对象）。

    Returns:
        算不算失败。
    """
    if getattr(record, "status", "") in FAILURE_STATUSES:
        return True
    return _exit_code_of(record) not in (None, 0)


def _exit_code_of(record: Any) -> int | None:
    """从记录的结果里取退出码。

    执行层把 ``ExecutionResult.to_dict()`` 存进 ``result`` —— 退出码就在
    顶层。取不到就当没有（不是失败）。
    """
    result = getattr(record, "result", None)
    if not isinstance(result, dict):
        return None
    value = result.get("exit_code")
    return value if isinstance(value, int) else None


# 状态标记行 —— 它们说明"失败了"，但不说明"错在哪"。
#
# ``run_python`` 的正文第一行是"退出码: 1"；``execute`` 的正文最后一行是
# "[Command failed with exit code 1]"。这两行都是结论，不是原因。
_MARKER_LINES = ("退出码:", "[Command ", "[Output ")


def _failure_message(record: Any) -> str:
    """这次失败**错在哪** —— 一句话。

    记录里的 ``error_message`` 有值就用它（那是能力抛错时写下的）。
    没有的话 —— 退出码非零但工具本身"成功"的情况 —— 从结果正文里取
    **最后一行真正的内容**：traceback 的末行（``ValueError: ...``）
    通常就是原因所在。
    """
    message = str(getattr(record, "error_message", "") or "").strip()
    if message:
        return message
    result = getattr(record, "result", None)
    content = result.get("content") if isinstance(result, dict) else ""
    if not isinstance(content, str):
        return ""
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith(_MARKER_LINES)
    ]
    return lines[-1] if lines else ""


async def recent_failures(
    session: AsyncSession | None,
    thread_id: str,
    *,
    limit: int = MAX_INJECTED,
    capability_key: str | None = None,
) -> list[FailureNote]:
    """取本会话最近几次没成功的调用。

    没有数据库会话（直接调 API 的场景）或没有会话 ID 时返回空 ——
    反思是加分项，缺了不该报错。

    Args:
        session: 数据库会话。
        thread_id: 会话 ID。
        limit: 最多返回几条。
        capability_key: 只看某个能力；不给就看全部。

    Returns:
        按新的在前的失败列表。
    """
    if session is None or not thread_id:
        return []
    from app.repositories.invocation import InvocationRepository

    records = await InvocationRepository(session).list_recent_any(
        thread_id,
        limit=SCAN_WINDOW,
        capability_key=capability_key or None,
    )
    notes = [
        FailureNote(
            capability_key=record.capability_key,
            status=record.status,
            error_code=record.error_code or "",
            error_message=_failure_message(record),
            created_at=record.created_at,
            exit_code=_exit_code_of(record),
        )
        for record in records
        if is_failure(record)
    ]
    return notes[: max(1, limit)]


def render_failures(notes: list[FailureNote]) -> str:
    """渲染成可注入上下文的 XML 块。

    空列表返回空串 —— 不注入的空块只会白占位置。

    块里要说清**模型能拿这些做什么**：光列条目，模型只会读，
    不会因此换个做法。
    """
    if not notes:
        return ""
    lines = [
        "<recent_failures>",
        "本会话最近几次没成功的调用（你自己的记录）：",
    ]
    lines.extend(f"- {note.summary()}" for note in notes)
    lines.append(
        "别重复同样的调用 —— 换个做法，或调用 invocation_recent_failures 看完整报错。"
    )
    lines.append("</recent_failures>")
    return "\n".join(lines)


async def build_reflection_block(
    session: AsyncSession | None,
    thread_id: str,
    *,
    limit: int = MAX_INJECTED,
) -> str:
    """给这一轮注入的反思块。

    查不到不该让整轮对话挂掉 —— 反思是加分项，不是前提。
    """
    if session is None or not thread_id:
        return ""
    try:
        notes = await recent_failures(session, thread_id, limit=limit)
    except Exception:  # pragma: no cover - 查表失败不该拖垮对话
        return ""
    return render_failures(notes)


__all__ = [
    "MAX_INJECTED",
    "MAX_MESSAGE_CHARS",
    "SCAN_WINDOW",
    "FailureNote",
    "build_reflection_block",
    "is_failure",
    "recent_failures",
    "render_failures",
]
