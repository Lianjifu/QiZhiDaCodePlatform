"""宿主侧的 git 操作。

**为什么在宿主机上跑 git，而不是在沙箱容器里**：worktree 是宿主上的目录，
容器里没有 git、也挂不到宿主的工作树；而且 worktree 的元数据写在仓库的
``.git/worktrees/`` 里 —— 容器里做出来的 worktree 宿主根本用不了。

所以 git 操作是**平台能力**（``git_*`` 那几个 builtin），不是 agent 在
``execute`` 里敲的命令。沙箱镜像里没有 git，agent 想用 git 就调这些能力。

## 安全约定

- **参数数组**，不拼 shell 字符串 —— 分支名、路径都是外部输入，拼字符串就是
  命令注入的入口；
- **不在白名单里的路径一律不碰** —— 由调用方（环境服务）先校验，这里再确认
  一次目标确实是 git 仓库；
- 每次调用都有超时，输出截断 —— 防止一条 ``git log`` 把内存吃光。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# 默认超时（秒）。worktree 检出大仓库要久一点，所以单独放宽。
DEFAULT_TIMEOUT = 30
WORKTREE_TIMEOUT = 180

# 输出的最大长度 —— 超出截断。git 的输出可能很大（``git log`` 尤其）。
MAX_OUTPUT = 20000


class GitError(AppError):
    """git 操作失败。"""

    status_code = 400
    code = "GIT_ERROR"
    message = "git 操作失败"


@dataclass
class GitStatus:
    """一个工作目录的 git 状态。"""

    branch: str = ""
    dirty: bool = False
    files: list[str] = field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    is_repo: bool = False


@dataclass
class WorktreeInfo:
    """一个 worktree。"""

    path: str
    branch: str = ""
    head: str = ""
    detached: bool = False
    locked: bool = False


class GitService:
    """某个目录上的 git 操作。

    Args:
        root: 仓库目录或 worktree 目录。
        timeout: 单次调用的默认超时（秒）。
    """

    def __init__(self, root: str | Path, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.root = Path(root).expanduser()
        self.timeout = timeout

    # ---------- 基础 ----------

    def is_repo(self) -> bool:
        """这个目录是不是在一个 git 仓库里。"""
        code, _ = self._exec("rev-parse", "--is-inside-work-tree")
        return code == 0

    def repo_root(self) -> Path | None:
        """仓库根目录；不在仓库里就是 ``None``。"""
        code, output = self._exec("rev-parse", "--show-toplevel")
        if code != 0:
            return None
        text = output.strip()
        return Path(text) if text else None

    def common_dir(self) -> Path | None:
        """仓库的公共 git 目录（主仓库的 ``.git``）。

        worktree 里跑这个命令拿到的是**主仓库**的 ``.git`` 目录 —— 靠它能
        从一个 worktree 找回它属于哪个仓库。
        """
        code, output = self._exec(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        if code != 0:
            return None
        text = output.strip()
        return Path(text) if text else None

    def current_branch(self) -> str:
        """当前分支名；游离 HEAD 时返回空串。"""
        code, output = self._exec("rev-parse", "--abbrev-ref", "HEAD")
        if code != 0:
            return ""
        name = output.strip()
        return "" if name == "HEAD" else name

    def head_sha(self) -> str:
        """当前 HEAD 的短 sha。"""
        code, output = self._exec("rev-parse", "--short", "HEAD")
        return output.strip() if code == 0 else ""

    def status(self) -> GitStatus:
        """工作目录状态（分支、脏文件、与上游的差距）。"""
        if not self.is_repo():
            return GitStatus(is_repo=False)
        branch = self.current_branch()
        files: list[str] = []
        dirty = False
        _, porcelain = self._exec("status", "--porcelain")
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            dirty = True
            files.append(line[3:].strip())
        ahead = behind = 0
        code, counts = self._exec("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        parts = counts.split()
        if code == 0 and len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
        return GitStatus(
            branch=branch,
            dirty=dirty,
            files=files,
            ahead=ahead,
            behind=behind,
            is_repo=True,
        )

    def branch_list(self) -> list[str]:
        """本地分支列表。"""
        code, output = self._exec("branch", "--format=%(refname:short)")
        if code != 0:
            return []
        return [line.strip() for line in output.splitlines() if line.strip()]

    def is_valid_branch(self, name: str) -> bool:
        """分支名是否合法。

        交给 git 自己判定（``check-ref-format``）—— 它的规则比任何正则都准，
        而且跟 git 实际接受的名字是同一份。

        这个命令不需要仓库，在任何目录下都能跑。
        """
        try:
            completed = subprocess.run(  # noqa: S603
                ["git", "check-ref-format", "--branch", name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # pragma: no cover - 环境异常
            return False
        return completed.returncode == 0

    # ---------- worktree ----------

    def worktree_add(
        self,
        path: str | Path,
        branch: str,
        *,
        base: str | None = None,
        create_branch: bool = True,
    ) -> WorktreeInfo:
        """建一个 worktree。

        Args:
            path: worktree 落点（必须不存在或为空目录）。
            branch: 分支名。
            base: 基点分支/提交；不给就是当前 HEAD。
            create_branch: 分支不存在时是否顺手建出来。

        Returns:
            新 worktree 的信息。

        Raises:
            GitError: git 报错。
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if create_branch:
            # git worktree add -b <分支> <路径> [基点]
            args = ["worktree", "add", "-b", branch, str(target)]
        else:
            # 分支已经存在：git worktree add <路径> <分支>
            args = ["worktree", "add", str(target), branch]
        if base:
            args.append(base)
        self._run(*args, timeout=WORKTREE_TIMEOUT)
        return WorktreeInfo(path=str(target), branch=branch)

    def worktree_list(self) -> list[WorktreeInfo]:
        """列出仓库的所有 worktree。"""
        output = self._run("worktree", "list", "--porcelain", check=False)
        items: list[WorktreeInfo] = []
        current: WorktreeInfo | None = None
        for line in output.splitlines():
            text = line.strip()
            if text.startswith("worktree "):
                if current is not None:
                    items.append(current)
                current = WorktreeInfo(path=text[len("worktree ") :])
            elif current is None:
                continue
            elif text.startswith("branch "):
                current.branch = text[len("branch ") :].removeprefix("refs/heads/")
            elif text.startswith("HEAD "):
                current.head = text[len("HEAD ") :]
            elif text == "detached":
                current.detached = True
            elif text == "locked":
                current.locked = True
        if current is not None:
            items.append(current)
        return items

    def worktree_remove(self, path: str | Path, *, force: bool = False) -> None:
        """删一个 worktree。

        Args:
            path: worktree 目录。
            force: 有未提交改动时也删。

        Raises:
            GitError: 删除失败。
        """
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        self._run(*args, timeout=WORKTREE_TIMEOUT)

    def worktree_prune(self) -> None:
        """清理已经消失的 worktree 记录。"""
        self._run("worktree", "prune", check=False)

    # ---------- 提交与合并 ----------

    def commit(self, message: str) -> dict[str, object]:
        """提交当前目录的全部改动。

        没有改动就**不提交**（返回 ``committed=False``）—— 空提交没有意义。

        Args:
            message: 提交信息。

        Returns:
            ``{"committed": bool, "sha": str, "files": [...]}``。

        Raises:
            GitError: 提交失败（比如没有配 git 身份）。
        """
        self._run("add", "-A")
        status = self.status()
        if not status.dirty:
            return {"committed": False, "sha": self.head_sha(), "files": []}
        self._run("commit", "-m", message, *self._identity_args())
        return {
            "committed": True,
            "sha": self.head_sha(),
            "files": status.files,
        }

    def merge(self, branch: str, *, target: str | None = None) -> dict[str, object]:
        """把一个分支合进当前分支。

        Args:
            branch: 要合进来的分支。
            target: 目标分支；不给就是当前分支。

        Returns:
            ``{"merged": bool, "conflicts": [...], "message": str}``。
            合不上去（冲突）时 ``merged=False``，并列出冲突文件 —— **不抛异常**，
            冲突是正常结果，交给上层决定怎么办。
        """
        if target:
            self._run("checkout", target)
        output = self._run("merge", "--no-edit", branch, check=False)
        conflicts = [
            line[3:].strip()
            for line in output.splitlines()
            if line.startswith("UU ") or line.startswith("AA ")
        ]
        merged = not conflicts and "CONFLICT" not in output.upper()
        return {
            "merged": merged,
            "conflicts": conflicts,
            "message": output.strip()[:2000],
        }

    def checkout(self, ref: str, *args: str) -> None:
        """切到某个分支或提交。

        Args:
            ref: 分支名或提交。
            args: 额外的 git 参数（``-b`` 建分支等），放在 ref 前面。
        """
        self._run("checkout", *args, ref)

    # ---------- 内部 ----------

    def _identity_args(self) -> list[str]:
        """没有配 git 身份时给一份默认的。

        平台里的 agent 提交代码，用户往往还没在机器上配过 ``user.email`` ——
        没有它 ``git commit`` 会直接失败，worker 的产出就留不下来了。
        """
        email = self._run("config", "user.email", check=False).strip()
        if email:
            return []
        return ["-c", "user.name=QiZhiDa Agent", "-c", "user.email=agent@qzd.local"]

    def _run(
        self,
        *args: str,
        cwd: str | Path | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> str:
        """跑一条 git 命令。

        Args:
            args: 参数数组（**不拼字符串**）。
            cwd: 工作目录；不给就是本服务的根目录。
            timeout: 超时（秒）。
            check: 失败时是否抛异常。

        Returns:
            命令输出（stdout + stderr）。

        Raises:
            GitError: 命令失败且 ``check=True``。
        """
        code, output = self._exec(*args, cwd=cwd, timeout=timeout)
        if check and code != 0:
            raise GitError(f"git {args[0]} 失败：{output.strip()[:500] or '未知错误'}")
        return output

    def _exec(
        self,
        *args: str,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        """跑一条 git 命令，返回 (退出码, 输出)。

        不抛异常 —— 有些调用要的是"失败也算答案"（``is_repo``、``status``）。
        """
        argv = ["git", *args]
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                cwd=str(cwd or self.root),
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("找不到 git 可执行文件，git 能力不可用") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git 命令超时（>{timeout or self.timeout}s）：{args[0]}") from exc
        output = (completed.stdout or "") + (completed.stderr or "")
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n... (输出过长，已截断，共 {len(output)} 字符)"
        return completed.returncode, output


__all__ = [
    "DEFAULT_TIMEOUT",
    "GitError",
    "GitService",
    "GitStatus",
    "WORKTREE_TIMEOUT",
    "WorktreeInfo",
]
