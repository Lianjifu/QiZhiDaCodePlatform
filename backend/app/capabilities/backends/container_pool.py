"""容器池：按会话复用 docker 容器。

## 为什么要有池

原来是 ``docker run --rm`` —— **每次执行都是新容器**。后果是 agent 的
工作环境每次归零：

```text
第一次: export FOO=hello; cd /; pwd   ->  /  hello
第二次: echo $FOO=$FOO; pwd            ->  FOO=（空）  /path/to/ws
```

对 code agent 这是硬伤 —— 用户说"装一下 pnpm"、"设个环境变量"、"cd 到 src"，
agent 装完下一轮又没了。

复用之后容器常驻：**装好的依赖、容器里生成的文件、跑起来的后台进程**都留着。

⚠️ **环境变量与 ``cd`` 不留** —— 那是 ``docker exec`` 的固有语义：每次 exec
都是新进程，进程内的变量与工作目录带不到下一次。这两样要留就得改成"持久
shell + 管道"的实现（复杂，且 shell 状态跨请求容易失控），当前不做。

所以 agent 装依赖、跑构建这类操作是有效的；但"设个环境变量，下一条命令要用"
这种**同一条命令内**的用法才成立 —— agent 得把 export 和使用写在同一条命令里。

## 生命周期

```text
execute()
  → 取会话的容器（没有就建，建之前先探活）
  → docker exec <容器> sh <脚本>
```

| 事件 | 动作 |
| --- | --- |
| 第一次用到某会话 | ``docker run -d`` 建常驻容器 |
| 再次使用 | ``docker exec`` 复用 |
| 空闲超过 ``SANDBOX_IDLE_TIMEOUT`` | ``docker rm -f`` 回收 |
| 执行前探活发现容器没了 | 删掉重建 |
| 平台启动 | 清理上次残留的容器 |

## 为什么不按"会话结束"回收

平台里**没有"会话结束"这个事件** —— 没人调 ``adelete_thread``，没有会话表，
没有归档。跟记忆模块的短期记忆是同一个问题。

所以靠**空闲超时**：一段时间没人用就回收。这是唯一可靠的信号。

## 并发

同一会话的多个请求**串行**执行（一个 ``asyncio.Lock``）—— 容器里共享 cwd 和
环境变量，并行会打架。不同会话各自一个容器，可以并行。

## 名字

容器名带会话标识，平台重启后能按前缀清理残留：

```text
qzd-sbx-<会话名的安全形式>
```

Docker 的容器名必须匹配 ``[a-zA-Z0-9][a-zA-Z0-9_.-]*``，所以会话名要先
做一次安全过滤。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 容器名前缀：平台重启后按它清理残留
CONTAINER_PREFIX = "qzd-sbx-"

# 容器名的非法字符（docker 要求 [a-zA-Z0-9][a-zA-Z0-9_.-]*）
_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]")

# 常驻容器里跑的"睡着的"进程 —— 让容器活着但什么都不做
#
# 必须用 ``sh -c`` 包一层：docker 直接执行命令时**不走 shell 的 PATH 解析**，
# "sleep infinity" 会被当成可执行文件名去找，找不到就报
# "executable file not found in $PATH"。走 sh 才有 PATH。
KEEPALIVE_COMMAND = ("sh", "-c", "sleep infinity")


def container_name(session_id: str) -> str:
    """会话 → 容器名。

    Args:
        session_id: 会话标识。

    Returns:
        合法的 docker 容器名。
    """
    safe = _UNSAFE.sub("-", session_id).strip("-.")[:48]
    if not safe:
        safe = "default"
    return f"{CONTAINER_PREFIX}{safe}"


class ContainerPool:
    """按会话管理常驻 docker 容器。

    进程内的单例 —— 容器本身是外部资源（docker daemon 管），但"哪些容器还活着、
    什么时候回收"这份状态在本进程里。
    """

    def __init__(self, *, idle_timeout: int = 1800, max_containers: int = 32) -> None:
        """构造。

        Args:
            idle_timeout: 空闲多少秒后回收容器。
            max_containers: 同时存活的容器上限。
        """
        self._idle_timeout = idle_timeout
        self._max_containers = max_containers
        # 会话 → 锁，保证同一会话的 execute 串行
        self._locks: dict[str, asyncio.Lock] = {}
        # 会话 → 最后使用时间（单调时钟）
        self._last_used: dict[str, float] = {}

    def lock_for(self, session_id: str) -> asyncio.Lock:
        """取会话的执行锁（不存在就建）。"""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def touch(self, session_id: str) -> None:
        """记录会话刚被用过。"""
        self._last_used[session_id] = time.monotonic()

    def idle_seconds(self, session_id: str) -> float:
        """会话空闲了多久（秒）。没用过就是无限。"""
        last = self._last_used.get(session_id)
        if last is None:
            return float("inf")
        return time.monotonic() - last

    def enforce_capacity(self) -> list[str]:
        """容器数超上限时，回收最久没用的那些。

        常驻容器占内存 —— 上限是防止一堆会话同时活跃把机器撑爆。

        Returns:
            被回收的容器名。
        """
        excess = len(self._last_used) - self._max_containers
        if excess <= 0:
            return []
        # 最久没用的先走
        candidates = sorted(self._last_used.items(), key=lambda item: item[1])
        removed: list[str] = []
        for session_id, _last in candidates[:excess]:
            name = container_name(session_id)
            if remove_container(name):
                removed.append(name)
            self._last_used.pop(session_id, None)
            self._locks.pop(session_id, None)
        return removed

    def reap_idle(self, *, force: bool = False) -> list[str]:
        """回收空闲超时的容器。

        Args:
            force: True 时忽略空闲时间，回收全部记录过的会话。

        Returns:
            被回收的容器名。
        """
        removed: list[str] = []
        for session_id in list(self._last_used):
            if not force and self.idle_seconds(session_id) < self._idle_timeout:
                continue
            name = container_name(session_id)
            if remove_container(name):
                removed.append(name)
            self._last_used.pop(session_id, None)
            self._locks.pop(session_id, None)
        return removed

    def reap_all(self) -> list[str]:
        """回收本进程记录过的全部容器（平台关闭时用）。"""
        return self.reap_idle(force=True)


# 进程内的池单例
_POOL: ContainerPool | None = None


def get_pool() -> ContainerPool:
    """取容器池单例。"""
    global _POOL
    if _POOL is None:
        from app.core.config import settings

        _POOL = ContainerPool(
            idle_timeout=settings.sandbox_idle_timeout,
            max_containers=settings.sandbox_max_containers,
        )
    return _POOL


def remove_container(name: str) -> bool:
    """删掉一个容器。删不掉不算失败（可能已经没了）。"""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(  # noqa: S603
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("回收容器 %s 失败: %s", name, exc)
        return False
    return True


def cleanup_stale_containers() -> list[str]:
    """清理平台上所有带我们前缀的残留容器。

    平台重启后，上次进程留下的容器还在跑（没人回收它们）。启动时清一遍。

    Returns:
        被清理的容器名。
    """
    if shutil.which("docker") is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={CONTAINER_PREFIX}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("查询残留容器失败: %s", exc)
        return []

    names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    removed = [name for name in names if remove_container(name)]
    if removed:
        logger.info("清理残留容器 %d 个", len(removed))
    return removed


def container_exists(name: str) -> bool:
    """容器还在不在。"""
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def container_workspace(name: str, mount: str = "/workspace") -> Path | None:
    """从容器上读出它挂载的工作区路径。

    复用容器时需要知道"这个容器对应哪个工作区" —— 容器名只带会话标识，
    挂载点得从 docker 那里问。

    Args:
        name: 容器名。
        mount: 容器内的挂载点。

    Returns:
        宿主上的工作区路径；问不到返回 None。
    """
    if shutil.which("docker") is None:
        return None
    template = '{{range .Mounts}}{{if eq .Destination "' + mount + '"}}{{.Source}}{{end}}{{end}}'
    try:
        completed = subprocess.run(  # noqa: S603
            ["docker", "inspect", "-f", template, name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    source = completed.stdout.strip()
    return Path(source) if source else None


__all__ = [
    "CONTAINER_PREFIX",
    "KEEPALIVE_COMMAND",
    "ContainerPool",
    "cleanup_stale_containers",
    "container_exists",
    "container_name",
    "container_workspace",
    "get_pool",
    "remove_container",
]
