"""LangGraph checkpointer：负责多轮对话的上下文持久化。

不用 checkpointer 的话，agent 每轮都"失忆"；用了之后，同一个 thread_id
就能接着上次的工具调用结果继续说。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.core.config import settings


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[Any]:
    """打开一个 checkpointer 上下文，退出时自动关闭连接。"""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(settings.agent_checkpointer_db) as saver:
        await saver.setup()
        yield saver
