"""会话级轮次锁 —— 同一个会话的一轮对话不许有两个在跑。

## 为什么需要

checkpoint 存在**一个共享的 SQLite 文件**里（``app/agents/checkpointer.py``）。
同一个 ``thread_id`` 上两个请求并发跑，各写各的 checkpoint：
轻则互相覆盖（后写的把先写的覆盖掉，历史凭空少一段），
重则撞上 SQLite 的写锁直接报错。

更麻烦的是**审批恢复**：图挂在 checkpoint 里等人批，
这时候再来一轮对话会把那段历史搅乱 —— 恢复时拿到的是被搅乱后的图。

## 粒度

**按会话**，不是全局一把锁 —— 不同的会话本来就该能并行。
锁是**进程内**的（跟能力层的并发闸门一样）：多副本部署时每个副本各管各的，
跨副本的并发要靠数据库层的隔离。

## 用法

```python
async with turn_lock(thread_id):
    ...  # 这一轮
```

流式接口要特别小心：锁要**覆盖整个生成过程**，不是只覆盖第一个 yield。
所以流式的地方要在生成器里 acquire、在 finally 里 release。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# 每个会话一把锁 —— 按 thread_id 索引。
#
# **不清理**：会话数量有限（每个会话一个目录），锁对象的开销可以忽略。
# 清理反而有个坑：某个请求还持有旧锁时把它删掉，下一个请求会拿到**新锁**，
# 于是两个请求都认为自己独占 —— 互斥就失效了。
_locks: dict[str, asyncio.Lock] = {}

# 拿不到锁时等多久（秒）。
#
# 前端在会话忙的时候不会发第二个请求，所以正常情况下不会等 ——
# 这个超时是给「异常客户端」兜底的：等太久不如快速失败。
DEFAULT_TIMEOUT = 30.0


def _lock_for(thread_id: str) -> asyncio.Lock:
    """取这个会话的锁（没有就建一把）。"""
    lock = _locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[thread_id] = lock
    return lock


@asynccontextmanager
async def turn_lock(
    thread_id: str, *, timeout: float | None = DEFAULT_TIMEOUT
) -> AsyncIterator[None]:
    """这个会话的这一轮的独占权。

    Args:
        thread_id: 会话 ID。
        timeout: 等锁多久（秒）；``None`` 表示一直等。

    Raises:
        TimeoutError: 等不到锁。
    """
    lock = _lock_for(thread_id)
    try:
        if timeout is None:
            await lock.acquire()
        else:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(
            f"会话 {thread_id} 正忙（另一轮还没跑完），稍后再试"
        ) from exc
    try:
        yield
    finally:
        lock.release()


__all__ = ["turn_lock"]
