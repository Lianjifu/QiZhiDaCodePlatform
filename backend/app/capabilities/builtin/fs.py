"""文件类内置能力：读 / 写 / 改 / 移 / 拷 / 建目录 / 列 / 按名找 / 按内容搜 / 切目录。

对应 Claude Code 的 Read / Write / Edit / Glob / Grep / Bash(cd|mv|cp|mkdir)，
但全部限制在会话工作区内。

路径一律相对**当前目录**（`context.workdir`），当前目录由 `change_directory`
切换，默认是工作区根目录。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from app.capabilities.builtin.paths import WorkspaceError, resolve_in_workspace
from app.capabilities.context import ExecutionContext

_MAX_MATCHES = 200
_MAX_GREP_LINE = 200


def _workspace(context: ExecutionContext) -> Path:
    return Path(context.workspace)


def _base(context: ExecutionContext) -> Path:
    """相对路径的起点：当前目录。"""
    return context.workdir


def _relative(workspace: Path, target: Path) -> str:
    try:
        return str(target.relative_to(workspace.resolve()))
    except ValueError:  # pragma: no cover - 理论上不会发生
        return str(target)


def _resolve(context: ExecutionContext, path: str) -> Path:
    """相对当前目录解析路径，且必须落在工作区内。"""
    return resolve_in_workspace(_workspace(context), path, base=_base(context))


# ---------- read ----------


def make_read_file(context: ExecutionContext) -> Callable[..., str]:
    """读取工作区内的文本文件内容。"""

    def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
        """读取工作区内的文本文件内容，带行号。path 是相对于当前目录的路径。

        offset/limit 可选，用于分段读取大文件（offset 从 0 开始，limit 是行数，
        0 表示读到文件末尾）。
        """
        target = _resolve(context, path)
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
        """把内容写入工作区内的文件（覆盖写）。path 是相对于当前目录的路径。"""
        target = _resolve(context, path)
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
        target = _resolve(context, path)
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


# ---------- move / copy / mkdir ----------


def make_move_file(context: ExecutionContext) -> Callable[..., str]:
    """移动或重命名文件/目录。"""

    def move_file(source: str, destination: str) -> str:
        """移动或重命名文件。

        source 与 destination 都相对于当前目录。destination 是已存在的目录时，
        移动到该目录下（与 shell 的 mv 一致）；否则 destination 就是目标路径。
        目标所在目录不存在会自动创建。
        """
        workspace = _workspace(context)
        src = _resolve(context, source)
        if not src.exists():
            return f"源不存在: {source}"
        dst = _resolve(context, destination)
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            return f"目标已存在，没有覆盖: {destination}"
        if src.is_dir() and (dst == src or src in dst.parents):
            return "不能把目录移动到它自己里面"
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            return f"移动失败: {exc}"
        return f"已移动 {source} → {_relative(workspace, dst)}"

    return move_file


def make_copy_file(context: ExecutionContext) -> Callable[..., str]:
    """复制文件或目录。"""

    def copy_file(source: str, destination: str) -> str:
        """复制文件或目录。

        source 与 destination 都相对于当前目录。destination 是已存在的目录时，
        复制到该目录下（与 shell 的 cp 一致）；否则 destination 就是目标路径。
        目标所在目录不存在会自动创建。
        """
        workspace = _workspace(context)
        src = _resolve(context, source)
        if not src.exists():
            return f"源不存在: {source}"
        dst = _resolve(context, destination)
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            return f"目标已存在，没有覆盖: {destination}"
        if src.is_dir() and (dst == src or src in dst.parents):
            return "不能把目录复制到它自己里面"
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
        except OSError as exc:
            return f"复制失败: {exc}"
        return f"已复制 {source} → {_relative(workspace, dst)}"

    return copy_file


def make_make_dir(context: ExecutionContext) -> Callable[..., str]:
    """创建目录。"""

    def make_dir(path: str) -> str:
        """创建目录（含多级父目录），已存在也算成功。

        path 相对于当前目录。
        """
        target = _resolve(context, path)
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        verb = "已存在" if existed else "已创建"
        return f"目录{verb}: {path}"

    return make_dir


# ---------- cd ----------


def make_change_directory(context: ExecutionContext) -> Callable[..., str]:
    """切换当前工作目录。"""

    def change_directory(path: str) -> str:
        """切换当前工作目录，之后所有相对路径都相对于新目录解析。

        path 相对于当前目录：\".\" 是当前目录，\"~\" 是工作区根目录，
        \"..\" 往上一级（不能越出工作区）。当前目录会在同一会话的多次请求之间保持。
        """
        workspace = _workspace(context)
        if path.strip() == "~":
            target = workspace.resolve()
        else:
            target = _resolve(context, path)
        if not target.exists():
            return f"目录不存在: {path}"
        if not target.is_dir():
            return f"不是目录: {path}"
        context.set_cwd(target)
        relative = _relative(workspace, target)
        return f"当前目录已切换到 {relative or '.'}"

    return change_directory


# ---------- list ----------


def make_list_files(context: ExecutionContext) -> Callable[..., str]:
    """列出工作区内的文件与目录。"""

    def list_files(path: str = ".") -> str:
        """列出目录下的文件和子目录。path 相对于当前目录，默认当前目录。"""
        workspace = _workspace(context)
        target = _resolve(context, path)
        if not target.exists():
            return f"目录不存在: {path}"
        if not target.is_dir():
            return f"不是目录: {path}"
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        header = f"当前目录: {_relative(workspace, target) or '.'}"
        if not entries:
            return f"{header}\n(空目录)"
        body = "\n".join(entry.name + "/" if entry.is_dir() else entry.name for entry in entries)
        return f"{header}\n{body}"

    return list_files


# ---------- glob ----------


def make_glob(context: ExecutionContext) -> Callable[..., str]:
    """按 glob 模式查找文件。"""

    def glob(pattern: str, path: str = ".") -> str:
        """按 glob 模式查找文件，返回相对于工作区根目录的路径列表。

        pattern 用 shell 通配语法，例如 **/*.py、src/**/test_*.py。
        path 相对于当前目录，默认为当前目录。
        """
        workspace = _workspace(context)
        root = _resolve(context, path)
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
        path 相对于当前目录，默认为当前目录。
        """
        workspace = _workspace(context)
        root = _resolve(context, path)
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
    "make_change_directory",
    "make_copy_file",
    "make_edit_file",
    "make_grep",
    "make_glob",
    "make_list_files",
    "make_make_dir",
    "make_move_file",
    "make_read_file",
    "make_write_file",
]
