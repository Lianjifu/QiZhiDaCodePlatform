"""编译好的图 —— 按会话缓存。

建一张图不便宜：十几个工具、十来个中间件，再加一次编译，实测 ~100ms。
同一个会话每轮都要建一次，而这一轮里其实**什么都没变** —— 工具集、技能、
后端、权限模式都一样，变的只有提示词里的两块内容（记忆目录、上传件清单），
那两块已经被挪到模型调用时才注进去（见 ``dynamic_context.py``）。

所以这里按会话缓存：**变了就重建，没变就复用**。

## 缓存键必须盖住所有影响图行为的东西

漏掉一项，后果不是"慢一点"，而是"用错了规则"：

- **权限模式** —— 决定哪些工具要人批。用户在会话中途把模式从"自动"改成
  "手动"，缓存还按老模式放行，就等于把用户刚收紧的闸门又打开了；
- **能力清单** —— 工具集与审批规则都从它来；
- **技能** —— 技能的来源目录被编进了图；
- **模型** —— 模型对象被编进了图，换模型必须重建；
- **工作区** —— 后端挂的是哪个目录。

## 为什么一次请求里不会撞车

同一个会话的轮次是**串行**的（`agents/turn_lock.py`）—— 缓存项里的执行
上下文会被就地刷新（换数据库会话、能力清单、模型），并发跑就会串味。

缓存是**进程内**的：多进程部署时每个进程各有一份，谁也不用等谁 ——
checkpoint 在文件里，图重建也就多花 100ms。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.agents.dynamic_context import PromptHolder
from app.capabilities.execution.context import ExecutionContext

# 最多缓存几张图 —— 每张都占着工具、中间件与后端，不能无限攒
MAX_ENTRIES = 16


@dataclass
class GraphEntry:
    """缓存里的一项。

    - ``agent`` —— 编译好的图；
    - ``skills`` —— 本次挂载的技能（跟着图一起复用）；
    - ``context`` —— 执行上下文（**会被就地刷新**，见 ``refresh``）；
    - ``holder`` —— 每轮变化的那两块提示词内容。
    """

    agent: Any
    skills: list[Any] = field(default_factory=list)
    context: ExecutionContext | None = None
    holder: PromptHolder = field(default_factory=PromptHolder)

    def refresh(
        self,
        *,
        session: Any,
        specs: list[Any],
        model: Any,
        mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        """把上下文里**每请求都会变**的部分换成这次的。

        执行上下文里混着两类东西：

        - **跟会话走**的：工作区、会话目录、线程 ID、信任模式 —— 变了就该重建；
        - **跟请求走**的：数据库会话、能力清单、模型、MCP 通道 —— 每次请求
          都可能是新的，不刷新就会往**上一次请求那个已经关掉的数据库会话**
          里写调用记录。

        只换后者，前者由缓存键保证没变。
        """
        if self.context is None:
            return
        self.context.session = session
        self.context.specs = list(specs)
        self.context.model = model
        if mcp_servers is not None:
            self.context.mcp_servers = dict(mcp_servers)


class GraphCache:
    """按会话缓存编译好的图。"""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: OrderedDict[Any, GraphEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: Any) -> GraphEntry | None:
        """取缓存项（命中就把它挪到最新）。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: Any, entry: GraphEntry) -> None:
        """存进去，超了就淘汰最久没用的那张。"""
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def drop(self, key: Any) -> None:
        """删掉一项（会话被删、能力变了……）。"""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """全清（测试用）。"""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# 进程内唯一一份
GRAPH_CACHE = GraphCache()


__all__ = ["GRAPH_CACHE", "MAX_ENTRIES", "GraphCache", "GraphEntry"]
