"""文件类内置能力：读 / 写 / 改 / 列 / 按名找 / 按内容搜。

对应 Claude Code 的 Read / Write / Edit / Glob / Grep，但全部限制在会话工作区内。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from app.capabilities.builtin.paths import WorkspaceError, resolve_in_workspace
from app.capabilities.context import ExecutionContext

_MAX_MATCHES = 200
_MAX_GREP_LINE = 200


def _workspace(context: ExecutionContext) -> Path:
    return Path(context.workspace)


def _relative(workspace: Path, target: Path) -> str:
    try:
        return str(target.relative_to(workspace.resolve()))
    except ValueError:  # pragma: no cover - 理论上不会发生
        return str(target)


# ---------- read ----------


def make_read_file(context: ExecutionContext) -> Callable[..., str]:
    """读取工作区内的文本文件内容。"""

    def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
        """读取工作区内的文本文件内容，带行号。path 是相对于工作区根目录的路径。

        offset/limit 可选，用于分段读取大文件（offset 从 0 开始，limit 是行数，
        0 表示读到文件末尾）。
        """
        workspace = _workspace(context)
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            return f"文件不存在: {path}"
        if not target.is_file():
            return f"不是文件: {path}"
        data = target.read_bytes()
        if len(data) > context.max_file_bytes:
            return (
                f"文件过大（{len(data)} 字节），超过上限 {context.max_file_bytes} 字节，"
                "请用 offset/limit 分段读取"
            )
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if offset or limit:
            start = max(offset, 0)
            end = start + limit if limit else len(lines)
            lines = lines[start:end]
        if not lines:
            return "(空文件)"
        width = len(str(len(lines)))
        return "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(lines, start=1))

    return read_file


# ---------- write ----------


def make_write_file(context: ExecutionContext) -> Callable[..., str]:
    """把内容写入工作区内的文件。"""

    def write_file(path: str, content: str) -> str:
        """把内容写入工作区内的文件（覆盖写）。path 是相对于工作区根目录的路径。"""
        workspace = _workspace(context)
        target = resolve_in_workspace(workspace, path)
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        verb = "覆盖" if existed else "创建"
        return f"已{verb} {path}（{len(content)} 字符）"

    return write_file


# ---------- edit ----------


def make_edit_file(context: ExecutionContext) -> Callable[..., str]:
    """对文件做精确字符串替换。"""

    def edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """把文件里的 old_string 精确替换成 new_string。

        old_string 必须唯一（除非 replace_all=true），否则会报错并列出所有匹配位置。
        """
        workspace = _workspace(context)
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            return f"文件不存在: {path}"
        text = target.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return f"没有找到要替换的内容: {old_string[:80]!r}"
        if count > 1 and not replace_all:
            positions = [i for i in range(len(text)) if text.startswith(old_string, i)]
            lines = [text[:pos].count("\n") + 1 for pos in positions]
            return (
                f"要替换的内容出现了 {count} 次（行号 {lines}），"
                "请提供更精确的上下文，或设置 replace_all=true"
            )
        updated = text.replace(old_string, new_string, -1 if replace_all else 1)
        target.write_text(updated, encoding="utf-8")
        verb = "替换了全部" if replace_all else "替换了"
        return f"已{verb} {path} 中的 {count} 处内容"

    return edit_file


# ---------- list ----------


def make_list_files(context: ExecutionContext) -> Callable[..., str]:
    """列出工作区内的文件与目录。"""

    def list_files(path: str = ".") -> str:
        """列出工作区内某个目录下的文件和子目录。path 默认为工作区根目录。"""
        workspace = _workspace(context)
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            return f"目录不存在: {path}"
        if not target.is_dir():
            return f"不是目录: {path}"
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return "(空目录)"
        return "\n".join(f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries)

    return list_files


# ---------- glob ----------


def make_glob(context: ExecutionContext) -> Callable[..., str]:
    """按 glob 模式查找文件。"""

    def glob(pattern: str, path: str = ".") -> str:
        """按 glob 模式查找文件，返回相对工作区根目录的路径列表。

        pattern 用 shell 通配语法，例如 **/*.py、src/**/test_*.py。
        """
        workspace = _workspace(context)
        root = resolve_in_workspace(workspace, path)
        if not root.is_dir():
            return f"目录不存在: {path}"
        matches = sorted(root.glob(pattern))
        results = [_relative(workspace, match) for match in matches if match.is_file()]
        if not results:
            return f"没有匹配 {pattern} 的文件"
        if len(results) > _MAX_MATCHES:
            results = results[:_MAX_MATCHES]
            return "\n".join(results) + f"\n... (共匹配更多，已截断到 {_MAX_MATCHES} 条)"
        return "\n".join(results)

    return glob


# ---------- grep ----------


def make_grep(context: ExecutionContext) -> Callable[..., str]:
    """按正则搜索文件内容。"""

    def grep(pattern: str, path: str = ".", glob_pattern: str = "") -> str:
        """在工作区里按正则搜索文件内容，返回 文件:行号:内容 的形式。

        glob_pattern 可选，用来限定搜索哪些文件，例如 *.py。
        """
        workspace = _workspace(context)
        root = resolve_in_workspace(workspace, path)
        if not root.exists():
            return f"目录不存在: {path}"
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"正则表达式不合法: {exc}"

        results: list[str] = []
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            if glob_pattern and not file_path.match(glob_pattern):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    snippet = line.strip()[:_MAX_GREP_LINE]
                    results.append(f"{_relative(workspace, file_path)}:{number}: {snippet}")
                    if len(results) >= _MAX_MATCHES:
                        return "\n".join(results) + f"\n... (结果过多，已截断到 {_MAX_MATCHES} 条)"
        if not results:
            return f"没有匹配 {pattern} 的内容"
        return "\n".join(results)

    return grep


__all__ = [
    "WorkspaceError",
    "make_edit_file",
    "make_grep",
    "make_glob",
    "make_list_files",
    "make_read_file",
    "make_write_file",
]
