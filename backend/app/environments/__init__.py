"""工作目录（环境）。

一个**环境**就是 agent 干活的那个目录：白名单下的一个普通目录，或者某个
git 仓库派生出的 worktree。会话可以绑一个环境；没绑就用平台自带的会话工作区
（原有行为不变）。

模块分工：

- ``paths`` —— 白名单与路径规则（谁能当工作目录）；
- ``git``（``app/git/service.py``）—— 宿主侧的 git 操作；
- ``service`` —— 环境的发现、绑定、worktree 的建与删。
"""

from app.environments.paths import (
    is_allowed,
    repo_roots,
    require_allowed,
    worktree_path,
    worktree_root,
)

__all__ = [
    "is_allowed",
    "repo_roots",
    "require_allowed",
    "worktree_path",
    "worktree_root",
]
