"""宿主侧的 git 操作。

git 的能力全在 :mod:`app.git.service` 里 —— 之所以单独一层，是因为 git 是
**宿主机**操作（worktree 是宿主目录，沙箱镜像里也没有 git），与能力层
的沙箱执行是两条完全不同的路。
"""

from app.git.service import (
    GitError,
    GitService,
    GitStatus,
    WorktreeInfo,
)

__all__ = ["GitError", "GitService", "GitStatus", "WorktreeInfo"]
