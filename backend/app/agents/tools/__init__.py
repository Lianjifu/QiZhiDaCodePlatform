"""Agent 工具集。

工具是 agent 与真实世界之间的唯一通道，因此这里的每一条工具都必须：
- 限定作用范围（工作区 / 沙箱）；
- 对模型可见的描述与实际行为一致；
- 出错时把错误信息如实返回给模型，而不是抛异常中断整轮对话。
"""

from pathlib import Path

from app.agents.tools.code import build_code_tools
from app.agents.tools.fs import build_fs_tools
from app.agents.tools.paths import WorkspaceError, resolve_in_workspace


def build_tools(workspace: Path) -> list:
    """组装某个工作区下的全部工具。"""
    return [*build_fs_tools(workspace), *build_code_tools(workspace)]


__all__ = [
    "build_tools",
    "build_code_tools",
    "build_fs_tools",
    "resolve_in_workspace",
    "WorkspaceError",
]
