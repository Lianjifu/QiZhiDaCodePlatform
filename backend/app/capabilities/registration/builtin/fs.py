"""文件类内置能力：读 / 写 / 改 / 移 / 拷 / 建目录 / 列 / 按名找 / 按内容搜 / 切目录。

对应 Claude Code 的 Read / Write / Edit / Glob / Grep / Bash(cd|mv|cp|mkdir)，
但全部限制在会话工作区内。

路径一律相对**当前目录**（``context.workdir``），当前目录由 ``change_directory``
切换，默认是工作区根目录。

每个能力返回**结构化结果**（dict）：

- ``content`` —— 给模型看的文本，与改造前完全一致；
- 其余字段 —— 给程序看的（契约校验、日志、自动化消费）。

失败一律抛 ``CapabilityError``，不再返回"看起来像成功"的字符串。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.paths import WorkspaceError, resolve_in_workspace

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
    try:
        return resolve_in_workspace(_workspace(context), path, base=_base(context))
    except WorkspaceError as exc:
        raise CapabilityError(str(exc), code=ErrorCode.PERMISSION_DENIED) from exc


# ---------- read ----------


def make_read_file(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """读取工作区内的文本文件内容。"""

    def read_file(path: str, offset: int = 0, limit: int = 0) -> dict[str, Any]:
        """读取工作区内的文本文件内容，带行号。path 是相对于当前目录的路径。

        offset/limit 可选，用于分段读取大文件（offset 从 0 开始，limit 是行数，
        0 表示读到文件末尾）。
        """
        target = _resolve(context, path)
        if not target.exists():
            raise CapabilityError(f"文件不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        if not target.is_file():
            raise CapabilityError(f"不是文件: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        data = target.read_bytes()
        if len(data) > context.max_file_bytes:
            raise CapabilityError(
                f"文件过大（{len(data)} 字节），超过上限 {context.max_file_bytes} 字节，"
                "请用 offset/limit 分段读取",
                code=ErrorCode.EXECUTION_ERROR,
                retryable=True,
            )
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if offset or limit:
            start = max(offset, 0)
            end = start + limit if limit else total
            lines = lines[start:end]
        if not lines:
            return {"content": "(空文件)", "line_count": 0, "truncated": False}
        width = len(str(len(lines)))
        content = "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(lines, start=1))
        return {
            "content": content,
            "line_count": len(lines),
            "truncated": total > len(lines),
        }

    return read_file


# ---------- write ----------


def make_write_file(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """把内容写入工作区内的文件。"""

    def write_file(path: str, content: str) -> dict[str, Any]:
        """把内容写入工作区内的文件（覆盖写）。path 是相对于当前目录的路径。"""
        target = _resolve(context, path)
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        verb = "覆盖" if existed else "创建"
        return {
            "content": f"已{verb} {path}（{len(content)} 字符）",
            "path": path,
            "bytes": len(data),
            "created": not existed,
        }

    return write_file


# ---------- edit ----------


def make_edit_file(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """对文件做精确字符串替换。"""

    def edit_file(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """把文件里的 old_string 精确替换成 new_string。

        old_string 必须唯一（除非 replace_all=true），否则会报错并列出所有匹配位置。
        """
        target = _resolve(context, path)
        if not target.exists():
            raise CapabilityError(f"文件不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        text = target.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            raise CapabilityError(
                f"没有找到要替换的内容: {old_string[:80]!r}",
                code=ErrorCode.INVALID_ARGUMENTS,
                retryable=True,
            )
        if count > 1 and not replace_all:
            positions = [i for i in range(len(text)) if text.startswith(old_string, i)]
            lines = [text[:pos].count("\n") + 1 for pos in positions]
            raise CapabilityError(
                f"要替换的内容出现了 {count} 次（行号 {lines}），"
                "请提供更精确的上下文，或设置 replace_all=true",
                code=ErrorCode.INVALID_ARGUMENTS,
                retryable=True,
            )
        updated = text.replace(old_string, new_string, -1 if replace_all else 1)
        target.write_text(updated, encoding="utf-8")
        verb = "替换了全部" if replace_all else "替换了"
        return {
            "content": f"已{verb} {path} 中的 {count} 处内容",
            "path": path,
            "replaced_count": count,
        }

    return edit_file


# ---------- move / copy / mkdir ----------


def make_move_file(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """移动或重命名文件/目录。"""

    def move_file(source: str, destination: str) -> dict[str, Any]:
        """移动或重命名文件。

        source 与 destination 都相对于当前目录。destination 是已存在的目录时，
        移动到该目录下（与 shell 的 mv 一致）；否则 destination 就是目标路径。
        目标所在目录不存在会自动创建。
        """
        workspace = _workspace(context)
        src = _resolve(context, source)
        if not src.exists():
            raise CapabilityError(f"源不存在: {source}", code=ErrorCode.INVALID_ARGUMENTS)
        dst = _resolve(context, destination)
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise CapabilityError(
                f"目标已存在，没有覆盖: {destination}",
                code=ErrorCode.INVALID_ARGUMENTS,
            )
        if src.is_dir() and (dst == src or src in dst.parents):
            raise CapabilityError("不能把目录移动到它自己里面", code=ErrorCode.INVALID_ARGUMENTS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            raise CapabilityError(f"移动失败: {exc}") from exc
        return {
            "content": f"已移动 {source} → {_relative(workspace, dst)}",
            "source": source,
            "destination": _relative(workspace, dst),
        }

    return move_file


def make_copy_file(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """复制文件或目录。"""

    def copy_file(source: str, destination: str) -> dict[str, Any]:
        """复制文件或目录。

        source 与 destination 都相对于当前目录。destination 是已存在的目录时，
        复制到该目录下（与 shell 的 cp 一致）；否则 destination 就是目标路径。
        目标所在目录不存在会自动创建。
        """
        workspace = _workspace(context)
        src = _resolve(context, source)
        if not src.exists():
            raise CapabilityError(f"源不存在: {source}", code=ErrorCode.INVALID_ARGUMENTS)
        dst = _resolve(context, destination)
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise CapabilityError(
                f"目标已存在，没有覆盖: {destination}",
                code=ErrorCode.INVALID_ARGUMENTS,
            )
        if src.is_dir() and (dst == src or src in dst.parents):
            raise CapabilityError("不能把目录复制到它自己里面", code=ErrorCode.INVALID_ARGUMENTS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
        except OSError as exc:
            raise CapabilityError(f"复制失败: {exc}") from exc
        return {
            "content": f"已复制 {source} → {_relative(workspace, dst)}",
            "source": source,
            "destination": _relative(workspace, dst),
        }

    return copy_file


def make_make_dir(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """创建目录。"""

    def make_dir(path: str) -> dict[str, Any]:
        """创建目录（含多级父目录），已存在也算成功。

        path 相对于当前目录。
        """
        target = _resolve(context, path)
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        verb = "已存在" if existed else "已创建"
        return {
            "content": f"目录{verb}: {path}",
            "path": path,
            "created": not existed,
        }

    return make_dir


# ---------- cd ----------


def make_change_directory(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """切换当前工作目录。"""

    def change_directory(path: str) -> dict[str, Any]:
        """切换当前工作目录，之后所有相对路径都相对于新目录解析。

        path 相对于当前目录："." 是当前目录，"~" 是工作区根目录，
        ".." 往上一级（不能越出工作区）。当前目录会在同一会话的多次请求之间保持。
        """
        workspace = _workspace(context)
        if path.strip() == "~":
            target = workspace.resolve()
        else:
            target = _resolve(context, path)
        if not target.exists():
            raise CapabilityError(f"目录不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        if not target.is_dir():
            raise CapabilityError(f"不是目录: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        context.set_cwd(target)
        relative = _relative(workspace, target)
        return {
            "content": f"当前目录已切换到 {relative or '.'}",
            "cwd": relative or ".",
        }

    return change_directory


# ---------- list ----------


def make_list_files(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """列出工作区内的文件与目录。"""

    def list_files(path: str = ".") -> dict[str, Any]:
        """列出目录下的文件和子目录。path 相对于当前目录，默认当前目录。"""
        workspace = _workspace(context)
        target = _resolve(context, path)
        if not target.exists():
            raise CapabilityError(f"目录不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        if not target.is_dir():
            raise CapabilityError(f"不是目录: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        header = f"当前目录: {_relative(workspace, target) or '.'}"
        if not entries:
            return {"content": f"{header}\n(空目录)", "entries": []}
        body = "\n".join(entry.name + "/" if entry.is_dir() else entry.name for entry in entries)
        return {
            "content": f"{header}\n{body}",
            "entries": [
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                }
                for entry in entries
            ],
        }

    return list_files


# ---------- glob ----------


def make_glob(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """按 glob 模式查找文件。"""

    def glob(pattern: str, path: str = ".") -> dict[str, Any]:
        """按 glob 模式查找文件，返回相对于工作区根目录的路径列表。

        pattern 用 shell 通配语法，例如 **/*.py、src/**/test_*.py。
        path 相对于当前目录，默认为当前目录。
        """
        workspace = _workspace(context)
        root = _resolve(context, path)
        if not root.is_dir():
            raise CapabilityError(f"目录不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        matches = sorted(root.glob(pattern))
        results = [_relative(workspace, match) for match in matches if match.is_file()]
        if not results:
            return {
                "content": f"没有匹配 {pattern} 的文件",
                "matches": [],
            }
        if len(results) > _MAX_MATCHES:
            results = results[:_MAX_MATCHES]
            content = "\n".join(results) + f"\n... (共匹配更多，已截断到 {_MAX_MATCHES} 条)"
        else:
            content = "\n".join(results)
        return {"content": content, "matches": results}

    return glob


# ---------- grep ----------


def make_grep(context: ExecutionContext) -> Callable[..., dict[str, Any]]:
    """按正则搜索文件内容。"""

    def grep(pattern: str, path: str = ".", glob_pattern: str = "") -> dict[str, Any]:
        """在工作区里按正则搜索文件内容，返回 文件:行号:内容 的形式。

        glob_pattern 可选，用来限定搜索哪些文件，例如 *.py。
        path 相对于当前目录，默认为当前目录。
        """
        workspace = _workspace(context)
        root = _resolve(context, path)
        if not root.exists():
            raise CapabilityError(f"目录不存在: {path}", code=ErrorCode.INVALID_ARGUMENTS)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise CapabilityError(
                f"正则表达式不合法: {exc}", code=ErrorCode.INVALID_ARGUMENTS, retryable=True
            ) from exc

        matches: list[dict[str, Any]] = []
        lines_out: list[str] = []
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
                    relative = _relative(workspace, file_path)
                    lines_out.append(f"{relative}:{number}: {snippet}")
                    matches.append({"file": relative, "line": number, "text": snippet})
                    if len(matches) >= _MAX_MATCHES:
                        content_text = (
                            "\n".join(lines_out) + f"\n... (结果过多，已截断到 {_MAX_MATCHES} 条)"
                        )
                        return {"content": content_text, "matches": matches}
        if not matches:
            return {"content": f"没有匹配 {pattern} 的内容", "matches": []}
        return {"content": "\n".join(lines_out), "matches": matches}

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
