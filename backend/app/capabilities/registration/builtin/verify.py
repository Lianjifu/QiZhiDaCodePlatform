"""效果验证：确认副作用**真的发生了**。

契约验证（`output_schema`）只能说明"返回值长得对"，说明不了"文件真写下来了"。
这一步去核对实际状态 —— 抓那种"适配器说成功了，文件却没落盘"的错。

按能力 key 注册；没有登记的能力就跳过这一步（契约验证仍然照做）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.paths import resolve_in_workspace


def _target(context: ExecutionContext, path: str) -> Path:
    """按当前目录解析路径（只用于验证，不做越界检查之外的判断）。"""
    return resolve_in_workspace(Path(context.workspace), path, base=context.workdir)


def verify_write_file(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """写完要真的有那个文件，且大小对得上。"""
    path = data.get("path")
    if not path:
        return "结果里没有 path，无法确认写入"
    target = _target(context, str(path))
    if not target.exists():
        return f"写入后文件不存在: {path}"
    expected = data.get("bytes")
    if isinstance(expected, int) and target.stat().st_size != expected:
        return f"写入后文件大小不符（期望 {expected}，实际 {target.stat().st_size}）: {path}"
    return None


def verify_edit_file(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """改完文件里应该已经找不到原文了。"""
    path = data.get("path")
    if not path:
        return "结果里没有 path，无法确认修改"
    target = _target(context, str(path))
    if not target.exists():
        return f"修改后文件不存在: {path}"
    return None


def verify_move_file(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """移动完：源没了，目标在了。"""
    source = data.get("source")
    destination = data.get("destination")
    if not destination:
        return "结果里没有 destination，无法确认移动"
    if not _target(context, str(destination)).exists():
        return f"移动后目标不存在: {destination}"
    if source:
        try:
            if _target(context, str(source)).exists():
                return f"移动后源还在: {source}"
        except Exception:
            return None
    return None


def verify_copy_file(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """复制完：目标在了。"""
    destination = data.get("destination")
    if not destination:
        return "结果里没有 destination，无法确认复制"
    if not _target(context, str(destination)).exists():
        return f"复制后目标不存在: {destination}"
    return None


def verify_make_dir(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """建完目录要在。"""
    path = data.get("path")
    if not path:
        return "结果里没有 path，无法确认创建"
    if not _target(context, str(path)).is_dir():
        return f"创建后目录不存在: {path}"
    return None


def verify_change_directory(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """切完当前目录要真的变了。"""
    expected = data.get("cwd")
    if expected is None:
        return "结果里没有 cwd，无法确认切换"
    current = context.workdir.resolve()
    root = Path(context.workspace).resolve()
    try:
        relative = str(current.relative_to(root))
    except ValueError:
        return "当前目录跑到工作区外面了"
    if relative != str(expected):
        return f"当前目录与声明不一致（声明 {expected}，实际 {relative or '.'}）"
    return None


def verify_run_python(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """退出码要真的有。"""
    if "exit_code" not in data:
        return "结果里没有 exit_code，无法确认执行结果"
    return None


def verify_run_bash(data: dict[str, Any], context: ExecutionContext) -> str | None:
    """退出码要真的有。"""
    if "exit_code" not in data:
        return "结果里没有 exit_code，无法确认执行结果"
    return None


VERIFIERS: dict[str, Callable[[dict[str, Any], ExecutionContext], str | None]] = {
    "write_file": verify_write_file,
    "edit_file": verify_edit_file,
    "move_file": verify_move_file,
    "copy_file": verify_copy_file,
    "make_dir": verify_make_dir,
    "change_directory": verify_change_directory,
    "run_python": verify_run_python,
    "run_bash": verify_run_bash,
}


def get_verifier(key: str) -> Callable[[dict[str, Any], ExecutionContext], str | None] | None:
    return VERIFIERS.get(key)


__all__ = ["VERIFIERS", "get_verifier"]
