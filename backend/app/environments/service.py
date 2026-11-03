"""环境服务：发现、绑定、创建与删除工作目录。

一个**环境**是 agent 干活的那个目录：

- ``folder`` —— 白名单根目录下的普通目录；
- ``worktree`` —— 某个 git 仓库派生出的独立检出。

会话绑一个环境；没绑就用平台自带的会话工作区（原有行为不变）。

## 三条硬规矩

1. **路径必须在白名单里**（``app.environments.paths``）—— 平台没有鉴权，
   白名单是唯一的闸门；
2. **只有平台自己建的 worktree 能删** —— 用户自己 ``git worktree add`` 出来
   的目录我们不碰，免得删掉人家正在用的检出；
3. **删 worktree 前先确认它确实在平台的工作区里** —— 路径解析之后比较，
   软链接指向别处的一律拒绝。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import NotFoundError, ValidationError
from app.environments.naming import auto_branch, slugify, validate_branch
from app.environments.paths import (
    RESERVED_DIRNAMES,
    is_allowed,
    repo_roots,
    require_allowed,
    worktree_path,
    worktree_root,
)
from app.environments.store import (
    KIND_FOLDER,
    KIND_WORKTREE,
    Binding,
    clear_binding,
    load_binding,
    save_binding,
)
from app.git.service import GitService

logger = logging.getLogger(__name__)


@dataclass
class Environment:
    """一个可作为工作目录的环境。"""

    path: str
    name: str = ""
    kind: str = KIND_FOLDER
    is_repo: bool = False
    branch: str = ""
    repo: str | None = None
    dirty: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "is_repo": self.is_repo,
            "branch": self.branch,
            "repo": self.repo,
            "dirty": self.dirty,
        }


class EnvironmentService:
    """环境与绑定关系。"""

    # ---------- 发现 ----------

    def list_environments(self) -> list[Environment]:
        """列出白名单下可用的环境。

        扫两层：根目录本身 + 它的一级子目录（更深的不扫 —— 仓库可能很大）。
        平台自己建的 worktree 单独列（它们在隐藏目录里，正常扫描看不到）。

        Returns:
            环境列表，按路径排序。
        """
        found: dict[str, Environment] = {}

        for root in repo_roots():
            for candidate in self._candidate_dirs(root):
                env = self.describe(candidate, strict=False)
                if env is not None:
                    found[env.path] = env
                # 仓库自己的 worktree 也列出来（用户手动建的那些）
                if env is not None and env.is_repo:
                    for item in GitService(candidate).worktree_list():
                        if not is_allowed(item.path):
                            continue
                        child = self.describe(item.path, strict=False)
                        if child is not None:
                            found[child.path] = child

        # 平台建的 worktree
        for candidate in self._platform_worktrees():
            env = self.describe(candidate, strict=False)
            if env is not None:
                found[env.path] = env

        return sorted(found.values(), key=lambda item: item.path)

    def _candidate_dirs(self, root: Path) -> list[Path]:
        """根目录下值得列出来的目录。"""
        candidates = [root]
        try:
            children = sorted(root.iterdir())
        except OSError:  # pragma: no cover - 目录读不到
            return candidates
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name.startswith(".") or child.name in RESERVED_DIRNAMES:
                continue
            candidates.append(child)
        return candidates

    def _platform_worktrees(self) -> list[Path]:
        """平台建的 worktree 目录（``<worktree 根>/<仓库>/<分支>``）。"""
        base = worktree_root()
        if base is None or not base.is_dir():
            return []
        result: list[Path] = []
        try:
            for repo_dir in sorted(base.iterdir()):
                if not repo_dir.is_dir():
                    continue
                for item in sorted(repo_dir.iterdir()):
                    if item.is_dir() and not item.is_symlink():
                        result.append(item)
        except OSError:  # pragma: no cover
            return result
        return result

    # ---------- 描述 ----------

    def describe(self, path: str | Path, *, strict: bool = True) -> Environment | None:
        """描述一个目录作为环境时的样子。

        Args:
            path: 目录。
            strict: 目录不合法时是抛异常还是返回 ``None``。

        Returns:
            环境信息；``strict=False`` 时不合法就返回 ``None``。

        Raises:
            ValidationError: ``strict=True`` 且路径不合法。
        """
        try:
            resolved = require_allowed(path)
        except ValidationError:
            if strict:
                raise
            return None

        env = Environment(
            path=str(resolved),
            name=resolved.name,
            kind=KIND_WORKTREE if self._is_platform_worktree(resolved) else KIND_FOLDER,
        )
        git = GitService(resolved)
        if not git.is_repo():
            return env
        env.is_repo = True
        env.branch = git.current_branch()
        env.repo = self._main_repo(resolved)
        env.dirty = git.status().dirty
        return env

    def _is_platform_worktree(self, path: Path) -> bool:
        """这个目录是不是平台建的 worktree。"""
        base = worktree_root()
        try:
            path.relative_to(base)
        except ValueError:
            return False
        return True

    def _main_repo(self, path: Path) -> str | None:
        """worktree 所属的主仓库目录。"""
        common = GitService(path).common_dir()
        if common is None:
            return None
        # common dir 是主仓库的 .git 目录，父目录才是仓库根
        return str(Path(common).parent)

    # ---------- 绑定 ----------

    def current(self, thread_id: str) -> Binding | None:
        """这个会话当前绑的环境；没绑就是 ``None``。"""
        return load_binding(thread_id)

    def bind(self, thread_id: str, path: str | Path, *, label: str = "") -> Binding:
        """把会话绑到某个环境。

        Args:
            thread_id: 会话 ID。
            path: 目标目录。
            label: 用户给这个环境起的名字（新建时填的）；不给就是目录名。

        Returns:
            绑定记录。

        Raises:
            ValidationError: 路径不合法。
        """
        env = self.describe(path)
        if env is None:  # pragma: no cover - strict=True 时不会发生
            raise ValidationError(f"路径不合法：{path}")
        binding = Binding(
            path=env.path,
            kind=env.kind,
            name=label.strip() or env.name,
            branch=env.branch,
            repo=env.repo,
        )
        return save_binding(thread_id, binding)

    def unbind(self, thread_id: str) -> None:
        """解绑这个会话（回到平台自带的会话工作区）。"""
        clear_binding(thread_id)

    # ---------- worktree ----------

    def create_worktree(
        self,
        repo_path: str | Path,
        branch: str | None = None,
        *,
        base: str | None = None,
        reuse: bool = False,
    ) -> Environment:
        """从一个仓库派生出新的 worktree。

        Args:
            repo_path: 仓库目录（子目录也行，会归到仓库根）。
            branch: 分支名。**不给就自动生成** —— 选中仓库就该直接能用，
                不该逼着人先想好分支叫什么。
            base: 基点分支/提交；不给就是仓库当前 HEAD。
            reuse: 目标已存在时复用它（**幂等**）。

                复用是必要的：LangGraph 恢复挂起的节点时会把那一轮里的工具
                调用**整个重放一遍**，不幂等的调用就会做两遍。

        Returns:
            新 worktree 的环境信息。

        Raises:
            ValidationError: 仓库不合法、分支名非法。
            NotFoundError: 仓库不是 git 仓库。
        """
        repo = require_allowed(repo_path)
        git = GitService(repo)
        root = git.repo_root()
        if root is None:
            raise NotFoundError(f"不是一个 git 仓库：{repo}")
        name = validate_branch(branch or auto_branch(root.name))
        target = worktree_path(root.name, slugify(name.replace("/", "-")))
        # git 不允许 worktree 落在主仓库的工作树内部
        if root == target or root in target.parents:
            raise ValidationError("worktree 不能建在主仓库内部")
        if target.exists():
            if not reuse:
                raise ValidationError(f"worktree 已经存在：{target}")
            env = self.describe(target, strict=False)
            if env is None:
                raise ValidationError(f"worktree 已经存在但不可用：{target}")
            return env
        GitService(root).worktree_add(
            target,
            name,
            base=base,
            # 分支已经存在就别再 "-b" 建一次（git 会直接报错）
            create_branch=name not in GitService(root).branch_list(),
        )

        env = self.describe(target)
        if env is None:  # pragma: no cover - 刚建出来必然合法
            raise NotFoundError(f"worktree 创建后找不到：{target}")
        return env

    def remove_worktree(self, path: str | Path) -> None:
        """删掉平台建的 worktree。

        Args:
            path: worktree 目录。

        Raises:
            ValidationError: 这个 worktree 不是平台建的。
            NotFoundError: 目录不存在。
        """
        resolved = require_allowed(path)
        if not self._is_platform_worktree(resolved):
            raise ValidationError("只能删除平台创建的 worktree")
        if not resolved.exists():
            raise NotFoundError(f"worktree 不存在：{resolved}")
        env = self.describe(resolved, strict=False)
        repo = Path(env.repo) if env and env.repo else None
        if repo is not None and repo.exists():
            GitService(repo).worktree_remove(resolved, force=True)
            GitService(repo).worktree_prune()
        # git 有时会留下空目录（worktree remove 失败但目录已空），清掉
        if resolved.exists():
            shutil.rmtree(resolved, ignore_errors=True)


__all__ = ["Environment", "EnvironmentService"]
