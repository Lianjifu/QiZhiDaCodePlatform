"""平台后端：在 ``CompositeBackend`` 外面包一层。

## 为什么要有这一层

``CompositeBackend`` 的路由有个不一致：**``ls("/")`` 看不到子路由**。

实测（三个 backend：工作区、技能目录、产物目录）：

```text
ls('/')          -> ['/main.py']                     ← 看不到 /skills/、/.artifacts/
glob('**/*.md')  -> ['/.artifacts/h.md', '/skills/SKILL.md']
grep('y')        -> ['/skills/SKILL.md']
```

原因是 route ``"/"`` 命中后走的是"单 backend 分支"，root 聚合分支不可达。

**同一个后端集合、三种操作、三种可见性** —— 模型 ``ls /`` 看不到技能目录，
会以为技能不存在。所以这里补一层：``ls("/")`` 时把子路由作为**虚拟目录项**
加进结果。

只补 ``ls``，别的操作（``glob`` / ``grep``）本来就聚合所有 backend，不用动。
"""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)


class PlatformBackend(SandboxBackendProtocol):
    """给 ``CompositeBackend`` 补上"子路由可见性"。

    只改 ``ls``：``ls("/")`` 时把每条子路由的根作为目录条目补进去。

    其余方法原样转发 —— 不重复实现 backend 的逻辑。

    **必须继承 ``SandboxBackendProtocol``**：deepAgents 的
    ``supports_execution()`` 用 ``isinstance`` 判断能不能执行命令 ——
    只实现 ``BackendProtocol`` 的话，execute 工具会直接报"后端不支持执行"。
    """

    def __init__(self, inner: Any, *, routes: dict[str, Any]) -> None:
        """构造。

        Args:
            inner: 内部的 ``CompositeBackend``。
            routes: 路由表（与 inner 的一致），用来知道有哪些子路由。
        """
        self._inner = inner
        # 只关心根路由之外的（"/" 本身就是根）
        self._sub_routes = sorted(
            (prefix for prefix in routes if prefix not in ("", "/")),
            key=len,
            reverse=True,
        )

    def ls(self, path: str) -> LsResult:
        """列目录。根目录时补上子路由的虚拟目录项。"""
        return self._merge_virtual_dirs(self._inner.ls(path), path)

    async def als(self, path: str) -> LsResult:
        """异步列目录。"""
        return self._merge_virtual_dirs(await self._inner.als(path), path)

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

    # ---------- 其余方法原样转发 ----------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._inner.read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._inner.aread(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._inner.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._inner.awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await self._inner.aedit(file_path, old_string, new_string, replace_all=replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        return self._inner.delete(file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._inner.adelete(file_path)

    def grep(self, pattern: str, path: str | None = None, **kwargs: Any) -> GrepResult:
        return _dedupe_grep(self._inner.grep(pattern, path, **kwargs))

    async def agrep(self, pattern: str, path: str | None = None, **kwargs: Any) -> GrepResult:
        return _dedupe_grep(await self._inner.agrep(pattern, path, **kwargs))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return _dedupe_glob(self._inner.glob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return _dedupe_glob(await self._inner.aglob(pattern, path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        return self._inner.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        return await self._inner.aupload_files(files)

    def download_files(self, paths: list[str]) -> list[Any]:
        return self._inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[Any]:
        return await self._inner.adownload_files(paths)

    # ---------- 透传属性 ----------

    @property
    def routes(self) -> dict[str, Any]:
        """路由表。"""
        return self._inner.routes

    @property
    def default(self) -> Any:
        """默认后端（命令执行走它）。"""
        return self._inner.default

    @property
    def artifacts_root(self) -> str:
        """产物目录前缀。"""
        return self._inner.artifacts_root

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """执行命令（转发给默认后端）。"""
        return self._inner.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """异步执行命令。"""
        return await self._inner.aexecute(command, timeout=timeout)

    @property
    def id(self) -> str:
        """沙箱标识（转发给默认后端）。"""
        return self._inner.default.id


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
            str(item.get("line_text", "")),
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
