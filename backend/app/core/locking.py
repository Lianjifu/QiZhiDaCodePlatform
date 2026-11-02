"""可重入的异步锁。

## 为什么不用 ``asyncio.Lock``

``asyncio.Lock`` **不可重入** —— 同一个任务里第二次 ``acquire()`` 会死锁。

而我们的数据库锁必须可重入：记忆的写入会层层调用（``add`` → ``collection_id``
→ 写集合表），不重入就死锁了。

## 怎么算"重入"

**按任务算**：同一个 asyncio 任务里嵌套加锁，内层直接放行；别的任务照常排队。

锁本身仍然保证：**同一时刻只有一个任务在动数据库**。

## 用法

```python
lock = TaskLock()

async with lock:
    ...
```
"""

from __future__ import annotations

import asyncio
from types import TracebackType


class TaskLock:
    """可重入的异步锁（按任务算重入）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None
        self._depth = 0

    async def acquire(self) -> None:
        """拿锁。同一个任务重复拿是放行。"""
        task = asyncio.current_task()
        if self._owner is task and self._depth > 0:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    def release(self) -> None:
        """放锁。"""
        self._depth -= 1
        if self._depth <= 0:
            self._depth = 0
            self._owner = None
            self._lock.release()

    async def __aenter__(self) -> "TaskLock":
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def locked(self) -> bool:
        """当前是否有人在用这把锁。"""
        return self._lock.locked()


__all__ = ["TaskLock"]
