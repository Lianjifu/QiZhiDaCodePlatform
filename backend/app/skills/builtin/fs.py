"""文件类内置工具。

不重新实现一遍文件读写 —— 直接复用 `app.agents.tools.fs` 里的闭包工具，
从 StructuredTool 里取出底层函数，这样两处逻辑永远一致。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.agents.tools.fs import build_fs_tools


def _pick(workspace: Path, name: str):
    for tool in build_fs_tools(workspace):
        if tool.name == name:
            return tool
    raise KeyError(f"内置文件工具 {name} 不存在")


def make_read_file(context: dict) -> Callable[[str], str]:
    """读取工作区内的文本文件。"""
    return _pick(Path(context["workspace"]), "read_file").func


def make_write_file(context: dict) -> Callable[[str, str], str]:
    """把内容写入工作区内的文件。"""
    return _pick(Path(context["workspace"]), "write_file").func


def make_list_files(context: dict) -> Callable[..., str]:
    """列出工作区内的文件与目录。"""
    return _pick(Path(context["workspace"]), "list_files").func
