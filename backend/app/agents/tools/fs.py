"""文件工具：读、写、列目录，全部限制在单个会话的工作区内。

工具用工厂函数生成（而不是模块级常量），是为了把 workspace 闭包进去 ——
每个会话一个独立工作区，工具自然就只能看到自己的目录。
"""

from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.agents.tools.paths import WorkspaceError, resolve_in_workspace
from app.core.config import settings


def build_fs_tools(workspace: Path) -> list[BaseTool]:
    """生成绑定到指定工作区的文件工具集。"""

    @tool
    def read_file(path: str) -> str:
        """读取工作区内的文本文件内容。path 是相对于工作区根目录的路径。"""
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            return f"文件不存在: {path}"
        if not target.is_file():
            return f"不是文件: {path}"
        data = target.read_bytes()
        if len(data) > settings.agent_max_file_bytes:
            return (
                f"文件过大（{len(data)} 字节），"
                f"超过上限 {settings.agent_max_file_bytes} 字节，请分段读取或改用 grep"
            )
        return data.decode("utf-8", errors="replace")

    @tool
    def write_file(path: str, content: str) -> str:
        """把内容写入工作区内的文件（覆盖写）。path 是相对于工作区根目录的路径。"""
        target = resolve_in_workspace(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"

    @tool
    def list_files(path: str = ".") -> str:
        """列出工作区内某个目录下的文件和子目录。path 默认为工作区根目录。"""
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            return f"目录不存在: {path}"
        if not target.is_dir():
            return f"不是目录: {path}"
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return "(空目录)"
        return "\n".join(f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries)

    return [read_file, write_file, list_files]


__all__ = ["build_fs_tools", "WorkspaceError"]
