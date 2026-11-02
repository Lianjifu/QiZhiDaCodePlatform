"""这个会话现在在跑什么。

状态面板上「后台任务」那一段的数据源。

## 什么算"任务"

**正在执行的工具调用** —— 沙箱里跑命令、跑 Python、联网抓取这些。
它们可能跑很久（沙箱超时默认 60 秒），用户得能看见"现在到底在跑什么、
跑了多久了"，不然只能对着一个转圈的块猜。

## 状态存在哪儿

**进程内存里**（模块级单例），不落库。

- 它是**给用户看的实时视图**，不是审计记录 —— 落库没意义；
- 进程重启就清空 —— 重启后本来也没有在跑的任务；
- 多 worker 部署下每个 worker 只看得见自己那份 —— 已知限制（当前是单进程）。

要审计级别的"这次会话跑过什么"，看调用记录表（``invocation``）——
那是另一回事，落库的。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class RunningTask:
    """一个正在跑的工具调用。"""

    tool: str
    detail: str
    started_at: datetime

    @property
    def elapsed_ms(self) -> int:
        """已经跑了多久（毫秒）。"""
        return int((datetime.now(UTC) - self.started_at).total_seconds() * 1000)


@dataclass
class _ThreadState:
    """一个会话的任务状态。"""

    running: dict[str, RunningTask] = field(default_factory=dict)
    finished: int = 0


class TaskRegistry:
    """在跑的工具调用的登记簿。"""

    def __init__(self) -> None:
        self._states: dict[str, _ThreadState] = {}
        self._lock = threading.Lock()

    def start(self, thread_id: str, tool: str, detail: str = "") -> str:
        """登记一个开始跑的工具调用。

        Args:
            thread_id: 会话 ID。
            tool: 工具名。
            detail: 一句话说明（命令、文件路径…）。

        Returns:
            登记号 —— 结束时用它销账。
        """
        token = f"{tool}-{id(self)}-{len(self._states)}"
        state = self._state(thread_id)
        with self._lock:
            state.running[token] = RunningTask(
                tool=tool, detail=detail, started_at=datetime.now(UTC)
            )
        return token

    def finish(self, thread_id: str, token: str) -> None:
        """销账（工具跑完了，成功失败都算）。"""
        with self._lock:
            state = self._states.get(thread_id)
            if state is None:
                return
            if state.running.pop(token, None) is not None:
                state.finished += 1

    def snapshot(self, thread_id: str) -> tuple[list[RunningTask], int]:
        """这个会话现在在跑什么、跑完过多少。

        Returns:
            (正在跑的列表, 已完成的个数)。
        """
        with self._lock:
            state = self._states.get(thread_id)
            if state is None:
                return [], 0
            return list(state.running.values()), state.finished

    def _state(self, thread_id: str) -> _ThreadState:
        with self._lock:
            return self._states.setdefault(thread_id, _ThreadState())


# 进程内单例
REGISTRY = TaskRegistry()


def describe_call(name: str, args: dict[str, Any]) -> str:
    """工具调用 → 一句话说明（给面板显示）。

    挑最能说明"在干什么"的参数：命令、代码、路径、URL。
    """
    for key in ("command", "code", "file_path", "path", "url", "query", "pattern"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip().splitlines()[0]
            return text[:120]
    return ""


__all__ = ["REGISTRY", "RunningTask", "TaskRegistry", "describe_call"]
