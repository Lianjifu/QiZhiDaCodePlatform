"""代码执行类内置工具。

复用 `app.agents.tools.code` 的沙箱实现，async 版本从 StructuredTool 的
`coroutine` 属性取。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from app.agents.tools.code import build_code_tools


def make_run_python(context: dict) -> Callable[[str], Awaitable[str]]:
    """在工作区沙箱里执行一段 Python 代码。"""
    for tool in build_code_tools(Path(context["workspace"])):
        if tool.name == "run_python":
            if tool.coroutine is None:  # pragma: no cover - 防御性
                raise RuntimeError("run_python 没有异步实现")
            return tool.coroutine
    raise KeyError("内置代码工具 run_python 不存在")
