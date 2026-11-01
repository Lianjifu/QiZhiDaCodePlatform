"""LangGraph checkpointer：负责多轮对话的上下文持久化。

不用 checkpointer 的话，agent 每轮都"失忆"；用了之后，同一个 thread_id
就能接着上次的工具调用结果继续说。

## 为什么是文件，不是内存

**审批恢复是另一次 HTTP 请求。** agent 撞上要人批的调用时，图会挂在
checkpoint 里；等人工批准之后，前端发**第二个请求**来恢复它。两次请求之间
进程可能已经重启、也可能不是同一个 worker —— 所以 checkpoint 必须落在
**所有进程都看得到的地方**，内存里的 saver 撑不住。

用 SQLite 文件（`settings.agent_checkpointer_db`）就是这个道理。

## 生命周期

它是一个**上下文管理器**：每次请求打开一次、用完就关。

```python
async with open_checkpointer() as checkpointer:
    result = await runner.run(...)
```

连接不能跨请求复用（SQLite 连接不适合长持有），但**数据**是持久的 ——
存在文件里，下次请求再打开就连得上。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.config import settings


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[Any]:
    """打开一个 checkpointer 上下文，退出时自动关闭连接。

    Yields:
        LangGraph 的 ``AsyncSqliteSaver``（已建好表）。

    Note:
        ``setup()`` 每次都会跑 —— 它是幂等的（``CREATE TABLE IF NOT EXISTS``），
        代价只是一次 SQL。
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(settings.agent_checkpointer_db) as saver:
        await saver.setup()
        yield saver


__all__ = ["open_checkpointer"]
