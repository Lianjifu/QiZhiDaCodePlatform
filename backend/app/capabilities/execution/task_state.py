"""任务状态：置信度评分、重试阈值、阻塞判定。

模型一直调同一个能力调不对，有两种可能：

- **格式类错误**（参数没按 schema 填）—— 模型自己改一下就能对，小重试几次；
- **语义类错误**（参数合法但意图错了，比如文件根本不存在）—— 说明模型对任务
  的判断有问题，再试也是浪费。

所以两类错误分开记：格式类只回灌给模型自我修正（有次数上限），语义类除了
回灌还要**扣任务置信度**。置信度掉到阈值以下，或者格式类重试超限，任务就
标记为**阻塞**，转人工。

状态不是内存里的 —— 它从 `invocation` 表的调用记录算出来，所以跨进程、
跨请求都一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.errors import ErrorCode
from app.core.config import settings
from app.models.invocation import Invocation

# 置信度评分
CONFIDENCE_START = 100.0
# 格式类：模型改改参数就能对，扣得轻
FORMAT_PENALTY = 10.0
# 语义类：模型的判断本身有问题，扣得重
SEMANTIC_PENALTY = 25.0
# 成功一次给点回来
SUCCESS_REWARD = 5.0
# 低于这个值判定为阻塞
BLOCK_THRESHOLD = 40.0
# 同一个能力连续多少次格式类错误就转人工
MAX_FORMAT_RETRIES = 3
# 统计窗口：只看最近这么多条记录
WINDOW_SIZE = 20

# 格式类错误码：参数没按 schema 填，模型改一下就能对
FORMAT_ERROR_CODES = {ErrorCode.SCHEMA_INVALID.value}
# 语义类错误码：参数合法，但意图错了（文件不存在、路径不匹配…）
SEMANTIC_ERROR_CODES = {
    ErrorCode.INVALID_ARGUMENTS.value,
    ErrorCode.EXECUTION_ERROR.value,
    ErrorCode.OUTPUT_INVALID.value,
}


@dataclass
class TaskState:
    """一个任务（会话）当前的状态。"""

    confidence: float = CONFIDENCE_START
    format_errors: int = 0  # 连续格式类错误次数（针对同一个能力）
    semantic_errors: int = 0
    blocked: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 1),
            "format_errors": self.format_errors,
            "semantic_errors": self.semantic_errors,
            "blocked": self.blocked,
            "reason": self.reason,
        }


async def load_task_state(
    session: AsyncSession | None,
    thread_id: str | None,
    capability_key: str = "",
) -> TaskState:
    """从调用记录算出任务状态。"""
    state = TaskState()
    if session is None or not thread_id:
        return state

    stmt = (
        select(Invocation)
        .where(Invocation.thread_id == thread_id)
        .order_by(Invocation.id.desc())
        .limit(WINDOW_SIZE)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    counting = True

    for record in records:
        # 置信度：所有记录都算（成功回补，失败扣分）
        if record.status == "succeeded" and record.error_code is None:
            state.confidence = min(CONFIDENCE_START, state.confidence + SUCCESS_REWARD)
        elif record.error_code in FORMAT_ERROR_CODES:
            state.confidence -= FORMAT_PENALTY
        elif record.error_code in SEMANTIC_ERROR_CODES:
            state.confidence -= SEMANTIC_PENALTY
            state.semantic_errors += 1

        # 连续格式错误：只数该能力**最近一串**的格式错误。
        # 这个能力自己的非格式错误会把链打断；别的能力的记录不影响它。
        if record.capability_key == capability_key:
            if counting and record.error_code in FORMAT_ERROR_CODES:
                state.format_errors += 1
            else:
                counting = False

    state.confidence = max(0.0, state.confidence)
    block_threshold = settings.capability_confidence_block_threshold
    max_format_retries = settings.capability_max_format_retries
    if state.format_errors >= max_format_retries:
        state.blocked = True
        state.reason = f"同一能力连续 {state.format_errors} 次参数错误，已停止自动重试"
    elif state.confidence < block_threshold:
        state.blocked = True
        state.reason = f"任务置信度 {state.confidence:.0f} 低于阈值 {block_threshold:.0f}"
    return state


__all__ = [
    "BLOCK_THRESHOLD",
    "CONFIDENCE_START",
    "FORMAT_ERROR_CODES",
    "FORMAT_PENALTY",
    "MAX_FORMAT_RETRIES",
    "SEMANTIC_ERROR_CODES",
    "SEMANTIC_PENALTY",
    "TaskState",
    "load_task_state",
]
