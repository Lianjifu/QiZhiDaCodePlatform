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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

# 环境清单的缓存时长（秒）。
#
# **扫一遍要一秒多** —— 白名单下每个候选目录都要问 git（是不是仓库、当前
# 分支、有没有未提交的改动）。而仓库状态不是秒秒变的东西。
#
# 十几秒足够 —— 用户新建/删掉 worktree 之后会立刻标记过期（见
# ``invalidate_environment_cache``），不会看到过期的东西。
LIST_CACHE_TTL_SECONDS = 15.0

# 缓存里的环境列表。模块级 —— 服务对象是每次请求新建的，缓存得活得久一点。
_list_cache: list[Environment] | None = None
# 缓存是什么时候扫的 —— 超过 TTL 就当过期。
_list_cache_at: float = 0.0

# 扫目录时只允许一个线程在扫 —— 免得并发请求各扫一遍（白扫）。
_scan_lock = threading.Lock()

# 后台刷新只允许一个在跑。
_refresh_lock = threading.Lock()
_refreshing = False


def invalidate_environment_cache() -> None:
    """环境清单变了（新建/删掉 worktree）—— **标记为过期**。

    **不直接丢掉** —— 丢掉的话下一次列清单就得同步等一次扫描（一秒多），
    用户刚建完 worktree 回到列表页又要干等。留着旧数据、下次读的时候
    后台刷新，用户马上就能看到列表。
    """
    global _list_cache_at
    if _list_cache is not None:
        _list_cache_at = 0.0


def environment_list_is_stale() -> bool:
    """缓存里的清单是不是过期的（后台正在刷新）。

    前端拿它告诉用户"这是上一次的，正在更新"—— 别让人以为那就是最新的。
    """
    if _list_cache is None:
        return True
    return time.monotonic() - _list_cache_at >= LIST_CACHE_TTL_SECONDS


def clear_environment_cache() -> None:
    """把缓存整个丢掉（不只是标记过期）。

    换了一套白名单的时候用 —— 旧数据一点都不能留。
    """
    global _list_cache, _list_cache_at
    _list_cache = None
    _list_cache_at = 0.0


def _cached_environments(scan: Callable[[], list[Environment]]) -> list[Environment]:
    """扫环境清单 —— **先给旧数据，后台再刷**。

    三种情况：

    - **有新鲜的缓存** —— 直接给（微秒级）；
    - **有过期的缓存** —— 先给旧的，同时后台重扫（用户不等）；
    - **什么都没有**（服务刚起）—— 老老实实扫一遍。
    """
    global _list_cache, _list_cache_at
    cached = _list_cache
    if cached is not None:
        if time.monotonic() - _list_cache_at < LIST_CACHE_TTL_SECONDS:
            return cached
        _refresh_in_background(scan)
        return cached
    # 第一次：没有旧数据可给，只能扫
    with _scan_lock:
        # 等锁的时候可能已经有别人扫完了
        if _list_cache is not None:
            return _list_cache
        items = scan()
        _list_cache = items
        _list_cache_at = time.monotonic()
        return items


def _refresh_in_background(scan: Callable[[], list[Environment]]) -> None:
    """后台重扫一遍环境清单。

    不阻塞调用方 —— 调用方拿到的是旧数据，等刷新完了下一次读就是新的。
    """
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def worker() -> None:
        global _list_cache, _list_cache_at, _refreshing
        try:
            with _scan_lock:
                items = scan()
                _list_cache = items
                _list_cache_at = time.monotonic()
            logger.info("环境清单已刷新（%d 项）", len(items))
        except Exception:  # pragma: no cover - 后台刷新失败不该影响服务
            logger.warning("刷新环境清单失败", exc_info=True)
        finally:
            _refreshing = False

    threading.Thread(target=worker, name="env-list-refresh", daemon=True).start()


def warm_environment_cache() -> None:
    """服务启动时先把清单扫好（后台）。

    第一次读的人就不用等那一秒多了 —— 清单早就在那儿了。
    """
    if _list_cache is not None:
        return
    _refresh_in_background(EnvironmentService()._scan_environments)

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

        **结果有短缓存** —— 扫一遍要好几秒（每个目录都要问 git），而仓库
        状态不是秒秒变的东西。改了环境（新建/删掉 worktree）会立刻失效。

        Returns:
            环境列表，按路径排序。
        """
        return _cached_environments(self._scan_environments)

    def _scan_environments(self) -> list[Environment]:
        """真正去扫一遍白名单。"""
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
        # **一次 git 调用拿齐**（是不是仓库、当前分支、主仓库目录）——
        # 分开问是三次进程启动，扫十几个目录就是好几秒。
        info = git.repo_info()
        if info is None or not info.is_inside_work_tree:
            return env
        env.is_repo = True
        env.branch = info.branch
        env.repo = self._main_repo(info.common_dir)
        env.dirty = git.is_dirty()
        return env

    def _is_platform_worktree(self, path: Path) -> bool:
        """这个目录是不是平台建的 worktree。"""
        base = worktree_root()
        try:
            path.relative_to(base)
        except ValueError:
            return False
        return True

    def _main_repo(self, common_dir: str) -> str | None:
        """worktree 所属的主仓库目录。

        ``common_dir`` 是主仓库的 ``.git`` 目录，父目录才是仓库根。
        """
        if not common_dir:
            return None
        return str(Path(common_dir).parent)

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

    def browse(self, path: str | Path | None = None) -> dict[str, Any]:
        """浏览一个目录下的子目录。

        **这是给界面上的"选文件夹"用的** —— 白名单下可能有几十层目录，
        光给一个固定的下拉列表没法挑。

        路径必须落在白名单里（``require_allowed``）—— 目录浏览也是读宿主机
        的文件系统，白名单是唯一的闸门。

        Args:
            path: 要浏览的目录；不给就浏览第一个白名单根。

        Returns:
            ``{"path", "parent", "entries"}``。
        """
        if path is None:
            roots = repo_roots()
            if not roots:
                raise ValidationError("平台还没有配置可用的仓库目录（REPO_ROOTS）")
            current = roots[0]
        else:
            current = require_allowed(path)

        entries: list[dict[str, Any]] = []
        try:
            children = sorted(current.iterdir())
        except OSError as exc:
            raise ValidationError(f"目录读不了：{current}（{exc}）") from exc
        for child in children:
            if child.is_symlink() or not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in RESERVED_DIRNAMES:
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_repo": (child / ".git").exists(),
                }
            )

        # 上一级：还在白名单里才给
        parent: str | None = None
        up = current.parent
        if up != current and is_allowed(up):
            parent = str(up)

        return {"path": str(current), "parent": parent, "entries": entries}

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

        # 环境清单变了 —— 缓存作废，下一次列清单会重新扫
        invalidate_environment_cache()
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
        # 环境清单变了 —— 缓存作废
        invalidate_environment_cache()


__all__ = [
    "Environment",
    "EnvironmentService",
    "clear_environment_cache",
    "environment_list_is_stale",
    "invalidate_environment_cache",
    "warm_environment_cache",
]
