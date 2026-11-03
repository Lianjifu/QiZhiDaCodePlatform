"""工作目录（环境）的路径规则。

平台允许把**真实的代码仓库**绑成会话的工作目录。这条能力会在宿主机上
建目录、跑 git、写文件 —— 而平台目前没有鉴权。所以这里有一道白名单闸门：

**只有配置在 ``repo_roots`` 里的根目录下的路径，才能作为工作目录。**

## 为什么默认是「一个都不许」

``repo_roots`` 默认留空 —— 不配置就完全不能用这个功能。

反过来的默认值（"没配就允许任意路径"）在这个平台上等于把宿主机文件系统
开放出去：任何人都能让平台去读写自己的仓库、别的项目、甚至 ``~/.ssh``。
宁可要人显式配置一次。

## 两类允许的位置

| 位置 | 是什么 |
| --- | --- |
| ``repo_roots`` 下的路径 | 用户能绑成工作目录的仓库与目录 |
| ``worktree_root`` 下的路径 | 平台自己派生的 worktree |

worktree 单独放一个根，是因为**它要挂进沙箱容器**：容器运行时（Colima 只
共享 ``$HOME``、Docker Desktop 默认共享 ``/Users``）只能挂载被共享的路径。
默认落在工作区根下面，就落在共享区里了。

## 校验用 resolve 之后的结果

``Path.resolve()`` 会把软链接展开。所以判断"在不在允许范围内"时比较的是
**解析后的真实路径** —— 目录里放一个指向 ``/etc`` 的软链接也过不了关。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# 白名单根目录下的这些目录名是平台自己用的，不该出现在"可绑定的目录"列表里
RESERVED_DIRNAMES = {".git", ".qzd"}


def repo_roots() -> list[Path]:
    """配置里的白名单根目录（已解析、去重、忽略不存在的）。

    Returns:
        根目录列表；没配置时是空列表。
    """
    raw = (settings.repo_roots or "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        path = Path(text).expanduser()
        try:
            resolved = path.resolve()
        except OSError:  # pragma: no cover - 极端情况下 resolve 会失败
            logger.warning("白名单根目录不可用，已忽略: %s", text)
            continue
        if not resolved.is_dir():
            logger.warning("白名单根目录不存在，已忽略: %s", resolved)
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def worktree_root() -> Path:
    """worktree 的落点根目录。

    配置了 ``WORKTREE_ROOT`` 就用它；否则用 ``<工作区根>/worktrees`` ——
    工作区根通常在用户的家目录里，正好落在容器运行时的共享区中。

    Returns:
        根目录（不保证存在，用的时候再建）。
    """
    configured = (settings.worktree_root or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(settings.workspace_root).resolve() / "worktrees"


def _inside(path: Path, root: Path) -> bool:
    """``path`` 在 ``root`` 里面吗（含相等）。"""
    return path == root or root in path.parents


def is_allowed(path: str | Path) -> bool:
    """这个路径能不能作为工作目录。

    Args:
        path: 候选路径（可以是相对路径）。

    Returns:
        解析后落在白名单根目录下、或落在 worktree 根下，就是 ``True``。
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:  # pragma: no cover - 解析失败就当不通过
        return False
    if any(_inside(resolved, root) for root in repo_roots()):
        return True
    # worktree 根下也允许 —— 那是平台自己派生出来的检出
    return _inside(resolved, worktree_root())


def require_allowed(path: str | Path) -> Path:
    """校验并返回解析后的路径。

    Args:
        path: 候选路径。

    Returns:
        解析后的绝对路径。

    Raises:
        ValidationError: 不在允许范围内，或目录不存在。
    """
    if not repo_roots():
        raise ValidationError(
            "平台还没有配置可用的仓库目录（REPO_ROOTS），"
            "请先在 .env 里配置允许作为工作目录的根目录"
        )
    resolved = Path(path).expanduser().resolve()
    if not is_allowed(resolved):
        raise ValidationError(f"路径不在允许的目录范围内：{resolved}")
    if not resolved.is_dir():
        raise ValidationError(f"目录不存在：{resolved}")
    return resolved


def worktree_path(repo_name: str, slug: str) -> Path:
    """某个仓库某个分支的 worktree 落点。

    Args:
        repo_name: 仓库目录名（用来分组）。
        slug: 分支的 slug（文件名安全的那部分）。

    Returns:
        worktree 目录的完整路径。
    """
    return worktree_root() / repo_name / slug


__all__ = [
    "RESERVED_DIRNAMES",
    "is_allowed",
    "repo_roots",
    "require_allowed",
    "worktree_path",
    "worktree_root",
]
