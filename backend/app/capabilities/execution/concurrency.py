"""会话级并发闸门。

## 为什么要有

**模型一次回复里可以带多个工具调用**，LangGraph 会并发执行它们。每个
``execute`` / ``run_python`` 都会起一个沙箱进程 —— 一次回复里来十个并行调用，
就是十个沙箱同时跑。

docker 模式有 ``--memory 512m --cpus 1 --pids-limit 256`` 兜着，但那是
**单容器限制**：并发开 N 个容器时每个容器都有 512m，宿主机照样被拖垮。

所以要一个**会话级**的上限：``capability_max_concurrency``（默认 4）。

## 语义

- **按会话限**，不是全局 —— 一个会话自己占满 4 个名额，不该挡住别的会话；
- 拿不到名额就**排队等**，不是直接失败；
- 等待有上限（``capability_concurrency_wait_seconds``），超了按"执行层繁忙"
  失败 —— 可重试的那种。

## 已知限制

**进程内计数** —— 多副本部署时每个副本各自计数，不是全局配额。要跨副本限流
得换 Redis 之类的共享计数。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class _SessionGate:
    """一个会话的闸门。"""

    semaphore: asyncio.Semaphore
    running: int = 0
    waiting: int = 0


class ConcurrencyLimiter:
    """会话级并发闸门。"""

    def __init__(self, limit: int | None = None) -> None:
        self._limit = limit if limit is not None else settings.capability_max_concurrency
        self._gates: dict[str, _SessionGate] = {}

    @property
    def limit(self) -> int:
        """并发上限。<= 0 表示不限。"""
        return self._limit

    def _gate(self, thread_id: str) -> _SessionGate:
        gate = self._gates.get(thread_id)
        if gate is None:
            gate = _SessionGate(semaphore=asyncio.Semaphore(max(self._limit, 1)))
            self._gates[thread_id] = gate
        return gate

    async def acquire(self, thread_id: str, *, timeout: float | None = None) -> bool:
        """拿一个名额。

        Args:
            thread_id: 会话 ID（空串当成全局）。
            timeout: 等多久（秒）；不给就用配置里的。

        Returns:
            拿到名额返回 True；等超时返回 False（调用方该按"繁忙"失败）。
        """
        if self._limit <= 0:
            return True
        limit = timeout if timeout is not None else settings.capability_concurrency_wait_seconds
        gate = self._gate(thread_id or "global")
        gate.waiting += 1
        started = time.monotonic()
        try:
            await asyncio.wait_for(gate.semaphore.acquire(), timeout=limit)
        except TimeoutError:
            logger.warning(
                "会话 %s 的并发名额等不到（上限 %d，等了 %.1fs）",
                thread_id,
                self._limit,
                time.monotonic() - started,
            )
            return False
        finally:
            gate.waiting -= 1
        gate.running += 1
        return True

    def release(self, thread_id: str) -> None:
        """还回去。"""
        gate = self._gates.get(thread_id or "global")
        if gate is not None:
            gate.running = max(gate.running - 1, 0)
            gate.semaphore.release()

    def snapshot(self, thread_id: str) -> dict[str, int]:
        """这个会话现在的占用情况（给状态面板看）。"""
        gate = self._gates.get(thread_id)
        if gate is None:
            return {"limit": self._limit, "running": 0, "waiting": 0}
        return {"limit": self._limit, "running": gate.running, "waiting": gate.waiting}


# 进程内单例
LIMITER = ConcurrencyLimiter()


__all__ = ["LIMITER", "ConcurrencyLimiter"]
