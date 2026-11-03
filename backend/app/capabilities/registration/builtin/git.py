"""git 能力：状态、worktree、提交、合并。

**为什么 git 是平台能力而不是 agent 自己敲命令**：沙箱镜像里没有 git，
worktree 又是宿主机上的目录 —— agent 在 ``execute`` 里跑 ``git worktree add``
只会得到 "command not found"。所以 git 走宿主，由平台提供能力。

## 作用范围

这些能力只认**当前工作目录**（``context.workspace``）—— 也就是会话绑定的
那个环境。没绑环境时它们根本不会出现在模型的工具列表里（见
``app/agents/runner.py``），所以这里不用反复检查"有没有绑定"。

分支名、路径这些参数全部走 **参数数组**传给 git，不拼 shell 字符串。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.environments.service import EnvironmentService
from app.git.service import GitError, GitService

logger = logging.getLogger(__name__)


def _git(context: ExecutionContext) -> GitService:
    """当前工作目录上的 git 服务。

    Raises:
        CapabilityError: 当前目录不是 git 仓库。
    """
    service = GitService(context.workspace)
    if not service.is_repo():
        raise CapabilityError(
            "当前工作目录不是 git 仓库，git 能力不可用",
            code=ErrorCode.INVALID_ARGUMENTS,
        )
    return service


def _guard(action: str, func: Callable[[], Any]) -> Any:
    """把 git 的报错翻译成能力错误。

    Args:
        action: 动作名（给模型看的）。
        func: 要跑的函数。

    Returns:
        函数的返回值。

    Raises:
        CapabilityError: git 报错。
    """
    try:
        return func()
    except GitError as exc:
        raise CapabilityError(
            f"{action}失败：{exc.message}",
            code=ErrorCode.INVALID_ARGUMENTS,
        ) from exc


def make_git_status(context: ExecutionContext) -> Callable[[], Awaitable[dict[str, Any]]]:
    """构造"看当前 git 状态"能力。"""

    async def git_status() -> dict[str, Any]:
        """看当前工作目录的 git 状态。

        Returns:
            ``content`` 是给人看的状态说明；另有 ``branch`` / ``dirty`` /
            ``files`` 等结构化字段。
        """
        git = _git(context)
        status = git.status()
        lines = [
            f"当前分支：{status.branch or '（游离 HEAD）'}",
            f"工作区：{'有未提交的改动' if status.dirty else '干净'}",
        ]
        if status.ahead or status.behind:
            lines.append(f"与上游的差距：领先 {status.ahead}，落后 {status.behind}")
        if status.files:
            lines.append("改动的文件：")
            lines.extend(f"  - {item}" for item in status.files[:30])
        return {
            "content": "\n".join(lines),
            "branch": status.branch,
            "dirty": status.dirty,
            "files": status.files,
            "ahead": status.ahead,
            "behind": status.behind,
        }

    return git_status


def make_git_worktree_add(
    context: ExecutionContext,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造"开一个新 worktree"能力。"""

    async def git_worktree_add(branch: str, base: str | None = None) -> dict[str, Any]:
        """从当前仓库开一个新的 worktree（独立检出 + 新分支）。

        用来把活儿拆开：每个分支一份独立的工作目录，互不干扰。

        Args:
            branch: 新分支名（如 ``feat/login``）。
            base: 基点分支或提交；不给就是当前 HEAD。

        Returns:
            ``content`` 是说明文字；另有 ``path`` / ``branch`` 等字段。
        """
        git = _git(context)
        root = git.repo_root()
        if root is None:  # pragma: no cover - _git 已经确认过是仓库
            raise CapabilityError("不是一个 git 仓库", code=ErrorCode.INVALID_ARGUMENTS)
        service = EnvironmentService()
        env = _guard(
            "创建 worktree",
            lambda: service.create_worktree(root, branch, base=base),
        )
        text = (
            f"已创建 worktree：{env.path}\n"
            f"分支：{env.branch}\n"
            f"在新目录里干活，提交后可以合回主线。"
        )
        return {
            "content": text,
            "path": env.path,
            "branch": env.branch,
            "kind": env.kind,
        }

    return git_worktree_add


def make_git_worktree_list(context: ExecutionContext) -> Callable[[], Awaitable[dict[str, Any]]]:
    """构造"列 worktree"能力。"""

    async def git_worktree_list() -> dict[str, Any]:
        """列出当前仓库的所有 worktree。

        Returns:
            ``content`` 是列表；另有 ``worktrees`` 结构化字段。
        """
        git = _git(context)
        root = git.repo_root()
        items = _guard("列出 worktree", lambda: GitService(root).worktree_list())
        lines = [
            f"- {item.path}（分支 {item.branch or '游离 HEAD'}）" for item in items
        ]
        return {
            "content": "\n".join(lines) if lines else "这个仓库还没有 worktree。",
            "worktrees": [
                {"path": item.path, "branch": item.branch, "head": item.head}
                for item in items
            ],
        }

    return git_worktree_list


def make_git_worktree_remove(
    context: ExecutionContext,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造"删 worktree"能力。"""

    async def git_worktree_remove(path: str, force: bool = False) -> dict[str, Any]:
        """删掉一个平台创建的 worktree。

        Args:
            path: worktree 目录。
            force: 有未提交改动时也删。

        Returns:
            ``content`` 是结果说明。
        """
        service = EnvironmentService()
        _guard("删除 worktree", lambda: service.remove_worktree(Path(path)))
        return {"content": f"已删除 worktree：{path}", "path": path, "removed": True}

    return git_worktree_remove


def make_git_commit(context: ExecutionContext) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造"提交"能力。"""

    async def git_commit(message: str) -> dict[str, Any]:
        """把当前工作目录的全部改动提交一次。

        Args:
            message: 提交信息（写清楚这次改了什么）。

        Returns:
            ``content`` 是结果说明；另有 ``committed`` / ``sha`` 等字段。
        """
        git = _git(context)
        result = _guard("提交", lambda: git.commit(message))
        if not result["committed"]:
            text = "工作区没有改动，没有产生新提交。"
        else:
            text = f"已提交：{result['sha']}\n提交的文件：\n" + "\n".join(
                f"  - {item}" for item in result["files"][:30]
            )
        return {
            "content": text,
            "committed": bool(result["committed"]),
            "sha": str(result["sha"]),
            "files": result["files"],
        }

    return git_commit


def make_git_merge(context: ExecutionContext) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造"合并分支"能力。"""

    async def git_merge(branch: str, target: str | None = None) -> dict[str, Any]:
        """把一个分支合进当前分支（或指定分支）。

        冲突了不会抛异常 —— 会把冲突文件列出来，由上层决定怎么办。

        Args:
            branch: 要合进来的分支。
            target: 目标分支；不给就是当前分支。

        Returns:
            ``content`` 是结果说明；另有 ``merged`` / ``conflicts`` 等字段。
        """
        git = _git(context)
        result = _guard("合并分支", lambda: git.merge(branch, target=target))
        if result["merged"]:
            text = f"已把 {branch} 合进 {target or '当前分支'}。"
        else:
            conflicts = result["conflicts"]
            text = (
                f"合并 {branch} 出现冲突，需要人工处理。\n"
                f"冲突文件：\n" + "\n".join(f"  - {item}" for item in conflicts)
            )
        return {
            "content": text,
            "merged": bool(result["merged"]),
            "conflicts": result["conflicts"],
            "branch": branch,
        }

    return git_merge


__all__ = [
    "make_git_commit",
    "make_git_merge",
    "make_git_status",
    "make_git_worktree_add",
    "make_git_worktree_list",
    "make_git_worktree_remove",
]
