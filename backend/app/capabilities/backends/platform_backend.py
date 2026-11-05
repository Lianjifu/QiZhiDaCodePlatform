"""平台后端：``CompositeBackend`` + 两处平台补丁。

## 为什么必须**继承** ``CompositeBackend``（而不是包一层）

deepAgents 多处用 ``isinstance(backend, CompositeBackend)`` 决定产物目录的
落点（``middleware/filesystem.py:1993``、``middleware/summarization.py:739``）：

```python
artifacts_root = self.backend.artifacts_root if isinstance(self.backend, CompositeBackend) else "/"
```

只包不继承 → 判定为 False → 产物目录退化成 ``"/"``。而 ``"/"`` 落在**用户的
工作区**里 —— 被淘汰的对话历史（含用户说过的话、agent 读到的代码）会落进用户
的仓库，可能被 ``git commit`` 一起提交上去。

继承之后，这些判定自然成立，产物目录回到 ``/.artifacts/``（工作区外面）。

## 补的两件事

1. ``ls("/")`` 要看得见子路由。``CompositeBackend`` 的根聚合分支在存在
   ``"/"`` 路由时**不可达**（路由 ``"/"`` 先命中，走单 backend 分支）——
   模型 ``ls /`` 看不到技能目录，会以为技能不存在。
2. ``glob`` / ``grep`` 去重。默认后端（沙箱）与路由 ``"/"``（宿主
   ``FilesystemBackend``）指向同一个工作区，不去重的话每个文件出现两次。

其余方法全部继承 ``CompositeBackend`` —— 不重复实现它的逻辑。
"""

from __future__ import annotations

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import (
    GlobResult,
    GrepResult,
    LsResult,
)


class PlatformBackend(CompositeBackend):
    """``CompositeBackend`` 的平台版。

    只做两处补丁，其余原样继承：

    - ``ls`` / ``als``：根目录时把子路由作为虚拟目录项补进去；
    - ``glob`` / ``grep`` / ``aglob`` / ``agrep``：去掉重复项。
    """

    def __init__(self, default, routes, *, artifacts_root: str = "/") -> None:
        """构造。

        Args:
            default: 默认后端（命令执行走它）。
            routes: 路由表（路径前缀 → 后端）。
            artifacts_root: 产物目录前缀。必须是工作区**外面**的路径 ——
                被淘汰的对话历史会写到它下面。
        """
        super().__init__(default=default, routes=routes, artifacts_root=artifacts_root)
        # 只关心根路由之外的（"/" 本身就是根）
        self._sub_routes = sorted(
            (prefix for prefix in routes if prefix not in ("", "/")),
            key=len,
            reverse=True,
        )

    # ---------- ls：补子路由 ----------

    def ls(self, path: str) -> LsResult:
        """列目录。根目录时补上子路由的虚拟目录项。"""
        return self._merge_virtual_dirs(super().ls(path), path)

    async def als(self, path: str) -> LsResult:
        """异步列目录。"""
        return self._merge_virtual_dirs(await super().als(path), path)

    def _merge_virtual_dirs(self, result: LsResult, path: str) -> LsResult:
        """把子路由的根作为虚拟目录项补进根目录的列表。

        Args:
            result: 内部后端返回的结果。
            path: 请求的路径。

        Returns:
            补过目录项的结果。
        """
        if result.error or not result.entries:
            return result
        if not _is_root(path):
            return result

        known = {entry.get("path") for entry in result.entries}
        extra = [
            {"path": prefix, "is_dir": True} for prefix in self._sub_routes if prefix not in known
        ]
        if not extra:
            return result
        merged = [*result.entries, *extra]
        merged.sort(key=lambda entry: str(entry.get("path", "")))
        return LsResult(error=None, entries=merged)

    # ---------- 搜索：去重 ----------

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """搜索文件内容（去掉重复项）。"""
        return _dedupe_grep(super().grep(pattern, path, glob, max_count=max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """异步搜索文件内容（去掉重复项）。"""
        return _dedupe_grep(await super().agrep(pattern, path, glob, max_count=max_count))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """按名字模式找文件（去掉重复项）。"""
        return _dedupe_glob(super().glob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步按名字模式找文件（去掉重复项）。"""
        return _dedupe_glob(await super().aglob(pattern, path))

    # ---------- 透传属性 ----------

    @property
    def id(self) -> str:
        """沙箱标识（转发给默认后端）。"""
        return self.default.id


def _dedupe_glob(result: GlobResult) -> GlobResult:
    """去掉 glob 结果里的重复项。

    **为什么会有重复**：``CompositeBackend.glob`` 在 path 为 ``"/"`` 或 None 时
    搜**默认后端 + 所有路由**。而我们的默认后端（沙箱）与 route ``"/"``
    （宿主 FilesystemBackend）**指向同一个工作区** —— 每个文件于是出现两次。

    agent 用 glob 找产物时会看到一堆重复项。
    """
    matches = result.matches
    if not matches:
        return result
    seen: set[tuple[str, int | None]] = set()
    unique = []
    for item in matches:
        key = (str(item.get("path", "")), item.get("line_number"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    if len(unique) == len(matches):
        return result
    return GlobResult(
        error=result.error,
        matches=unique,
        truncated=result.truncated,
        truncation_reason=result.truncation_reason,
    )


def _dedupe_grep(result: GrepResult) -> GrepResult:
    """去掉 grep 结果里的重复项（原因同 :func:`_dedupe_glob`）。"""
    matches = result.matches
    if not matches:
        return result
    seen: set[tuple[str, int | None, str]] = set()
    unique = []
    for item in matches:
        key = (
            str(item.get("path", "")),
            item.get("line_number"),
            item.get("line_text", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    if len(unique) == len(matches):
        return result
    return GrepResult(
        error=result.error,
        matches=unique,
        truncated=result.truncated,
    )


def _is_root(path: str) -> bool:
    """是不是根路径。

    ``/`` 是根；``""`` / ``None`` 也算（框架里表示"全部"）。
    """
    if not path:
        return True
    normalized = path.rstrip("/")
    return normalized == ""


__all__ = ["PlatformBackend"]
