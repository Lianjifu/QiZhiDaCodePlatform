"""文件类内置能力。

不重新实现文件读写 —— 直接复用 `app.agents.tools.fs` 里的闭包工具，
从 StructuredTool 里取出底层函数，这样两处逻辑永远一致。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.agents.tools.fs import build_fs_tools
from app.capabilities.context import ExecutionContext


def _pick(workspace: Path, name: str):
    for tool in build_fs_tools(workspace):
        if tool.name == name:
            return tool
    raise KeyError(f"内置文件能力 {name} 不存在")


def make_read_file(context: ExecutionContext) -> Callable[[str], str]:
    """读取工作区内的文本文件。"""
    return _pick(Path(context.workspace), "read_file").func


def make_write_file(context: ExecutionContext) -> Callable[[str, str], str]:
    """把内容写入工作区内的文件。"""
    return _pick(Path(context.workspace), "write_file").func


def make_list_files(context: ExecutionContext) -> Callable[..., str]:
    """列出工作区内的文件与目录。"""
    return _pick(Path(context.workspace), "list_files").func
