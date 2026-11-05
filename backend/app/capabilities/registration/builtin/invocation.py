"""调用记录查询能力：让 agent 能看见"自己刚才卡在哪"。

对应自主反思的**最小可行形态**。

平台早就把每次调用的完整轨迹写进了 ``invocation`` 表 —— 但那张表只有人在
管理界面看得见。agent 自己查不到，于是它在同一轮里反复试同一个失败的做法：
不是它不长记，是**没有记可读**。

这个能力把那几条失败读出来给它：哪个能力、什么状态、错误码、完整报错。

只读、幂等、无副作用 —— 风险是 read，不触发任何审批。

**为什么还往上下文里自动注入一份**：见 ``app/agents/reflection.py``。
自动注入给的是一行摘要（提醒级别），这里的工具给的是完整报错（要细看时用）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.reflection import MAX_INJECTED, recent_failures
from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext

# 一次最多返回多少条。
#
# 10 条是"够看清一条脉络"的上限：更多的话，模型会花一堆 token 读一份
# 跟自己当前任务无关的旧账。
MAX_LIMIT = 10


def make_invocation_recent_failures(context: ExecutionContext) -> Callable[..., Any]:
    """构造"查最近失败"能力。"""

    async def invocation_recent_failures(
        limit: int = MAX_INJECTED,
        capability_key: str = "",
    ) -> dict[str, Any]:
        """列出本会话最近几次**没成功**的能力调用。

        用来避免重蹈覆辙 —— 同一件事换个做法，或先看清上次到底错在哪。

        Args:
            limit: 最多返回几条，默认 5，最多 10。
            capability_key: 只看某个能力（比如 ``run_python``）；不给就看全部。

        Returns:
            ``content`` 是给模型看的逐条描述；``items`` 是结构化字段
            （能力名 / 状态 / 错误码 / 报错 / 时间）。

        Raises:
            CapabilityError: 没有会话上下文（这不是会话内的调用）。
        """
        if not context.thread_id:
            raise CapabilityError(
                "查询调用记录需要在会话中使用（缺少会话上下文）。",
                code=ErrorCode.INVALID_ARGUMENTS,
            )
        if limit < 1 or limit > MAX_LIMIT:
            raise CapabilityError(
                f"limit 只能是 1~{MAX_LIMIT} 之间的整数",
                code=ErrorCode.INVALID_ARGUMENTS,
            )

        # **在锁里查** —— 工具是并行跑的，而 AsyncSession 不允许并发使用。
        async with context.db():
            notes = await recent_failures(
                context.session,
                context.thread_id,
                limit=limit,
                capability_key=capability_key or None,
            )

        if not notes:
            return {
                "content": "本会话没有失败过的调用记录。",
                "items": [],
            }
        lines = [
            f"本会话最近 {len(notes)} 次没成功的调用：",
            *(note.render() for note in notes),
        ]
        return {
            "content": "\n".join(lines),
            "items": [note.to_dict() for note in notes],
        }

    return invocation_recent_failures


__all__ = ["MAX_LIMIT", "make_invocation_recent_failures"]
