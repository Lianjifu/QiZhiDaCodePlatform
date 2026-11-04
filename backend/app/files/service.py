"""文件服务：上传、列表、下载、删除。

工作区里的文件分三类（见 :mod:`app.files.storage`）。这一层管**上传件**与
**工作产物**两类 —— 用户能上传、能下载，agent 也能用文件工具直接处理。

**路径安全不在这里做**：所有来自外部的虚拟路径都交给 deepAgents 的
``FilesystemBackend``（``virtual_mode=True``）解析 —— 它会挡掉 ``..`` 穿越、
``~`` 展开与软链接逃逸。我们只负责把它的错误翻译成 HTTP 语义。

## 为什么上传件落工作区里

放工作区外（像 skills / artifacts 那样）的话，agent 的文件工具**看不见**它 ——
用户传了文件，agent 却读不到，这个功能就没意义了。放工作区里，agent 用现成的
``read_file`` / ``grep`` / ``glob`` 直接就能处理，不需要新增任何 agent 能力。

代价是 agent 能改用户的原始上传件。对 code agent 来说这通常无所谓（用户就是
让 agent 改它），所以上传接口同时返回原始文件名、大小与 sha256 —— 用户始终
知道自己传的是什么。
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend

from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.files.storage import (
    UPLOADS_DIRNAME,
    guess_mime_type,
    human_size,
    is_image,
    sanitize_filename,
    unique_filename,
    uploads_dir,
)

logger = logging.getLogger(__name__)

# 上传件目录名（虚拟路径第一段）
UPLOADS_DIR = UPLOADS_DIRNAME


@dataclass(frozen=True)
class FileEntry:
    """工作区里的一个文件。"""

    path: str
    filename: str
    size: int
    mime_type: str
    is_dir: bool
    modified_at: datetime
    sha256: str = ""
    uploaded: bool = False


@dataclass(frozen=True)
class FileStat:
    """一个文件的状态。"""

    size: int
    is_dir: bool
    modified_at: datetime


@dataclass(frozen=True)
class UploadResult:
    """一次上传的结果。"""

    path: str
    filename: str
    size: int
    sha256: str
    mime_type: str
    is_image: bool
    uploaded_at: datetime


class FileService:
    """一个会话工作区上的文件操作。

    Args:
        workspace: 会话工作区目录。
        max_upload_bytes: 单个文件的上限（字节）。
        max_download_bytes: 单个文件的下载上限（字节）。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        max_upload_bytes: int = 50 * 1024 * 1024,
        max_download_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.workspace = Path(workspace)
        self.max_upload_bytes = max_upload_bytes
        self.max_download_bytes = max_download_bytes
        # 虚拟路径模式："/" 是工作区根，越界路径直接被拒
        self._backend = FilesystemBackend(root_dir=self.workspace, virtual_mode=True)

    # ---------- 上传 ----------

    def upload(
        self,
        filename: str,
        content: bytes,
        *,
        mime_type: str | None = None,
    ) -> UploadResult:
        """把一个文件存进工作区的 ``uploads/``。

        Args:
            filename: 客户端给的文件名。
            content: 文件内容。
            mime_type: 客户端给的 MIME 类型；没给就按文件名猜。

        Returns:
            上传结果（含虚拟路径、大小、哈希）。

        Raises:
            ValidationError: 文件名为空或超过大小上限。
        """
        safe = sanitize_filename(filename)
        if not safe:
            raise ValidationError("文件名不能为空")
        if not content:
            raise ValidationError("文件内容为空")
        if len(content) > self.max_upload_bytes:
            raise ValidationError(
                f"文件超过大小上限（{human_size(self.max_upload_bytes)}）"
            )

        stored = unique_filename(
            safe,
            exists=lambda name: (self.workspace / UPLOADS_DIR / name).exists(),
        )
        virtual_path = f"/{UPLOADS_DIR}/{stored}"
        responses = self._backend.upload_files([(virtual_path, content)])
        error = responses[0].error if responses else "unknown_error"
        if error:
            raise AppError(f"保存文件失败：{error}")

        return UploadResult(
            path=virtual_path,
            filename=stored,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=mime_type or guess_mime_type(stored),
            is_image=is_image(mime_type, stored),
            uploaded_at=datetime.now(UTC),
        )

    # ---------- 打包下载 ----------

    def build_archive(
        self,
        target: Path,
        *,
        max_file_bytes: int = 100 * 1024 * 1024,
    ) -> tuple[int, list[str]]:
        """把工作区打成 zip。

        **只打工作区里的东西** —— 平台产物（``.artifacts/``）在工作区外面，
        自然不会被打进去（里面有对话历史）。

        单个文件超上限就跳过并记下来，**不让整个包失败** —— 用户要的是
        "能拿走的那些"，不是"一个都别给我"。

        Args:
            target: zip 写到哪儿。
            max_file_bytes: 单个文件的体积上限。

        Returns:
            (打进去的文件数, 被跳过的文件说明)。
        """
        root = self.workspace
        skipped: list[str] = []
        count = 0
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            if not root.exists():
                return 0, skipped
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > max_file_bytes:
                    skipped.append(f"{relative.as_posix()}（{human_size(size)}，超过上限）")
                    continue
                try:
                    archive.write(path, arcname=relative.as_posix())
                except OSError as exc:
                    skipped.append(f"{relative.as_posix()}（{exc}）")
                    continue
                count += 1
        return count, skipped

    # ---------- 读取 ----------

    def list_files(
        self, *, include_uploads: bool = True, include_dirs: bool = False
    ) -> list[FileEntry]:
        """列出工作区里的文件（上传件 + agent 的产物）。

        只列工作区里的东西 —— 平台产物（``.artifacts/``）在工作区**外面**，
        自然不会出现在这里。

        Args:
            include_uploads: 是否列出上传件目录。
            include_dirs: 是否把目录也列进来。@ 提及要能选目录，所以那边要 True。

        Returns:
            文件列表，按路径排序。
        """
        entries: list[FileEntry] = []
        root = self.workspace
        if not root.exists():
            return entries
        uploads = uploads_dir(root)
        for path in sorted(root.rglob("*")):
            # 软链接可能指向工作区外面 —— 列表里不给它，免得泄露工作区外的存在
            if path.is_symlink():
                continue
            if path.is_dir():
                if not include_dirs:
                    continue
                if not include_uploads and uploads in path.parents:
                    continue
                relative = path.relative_to(root)
                entries.append(
                    FileEntry(
                        path=f"/{relative.as_posix()}",
                        filename=path.name,
                        size=0,
                        mime_type="inode/directory",
                        is_dir=True,
                        modified_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                        uploaded=uploads in path.parents,
                    )
                )
                continue
            if not path.is_file():
                continue
            if not include_uploads and uploads in path.parents:
                continue
            stat = path.stat()
            relative = path.relative_to(root)
            entries.append(
                FileEntry(
                    path=f"/{relative.as_posix()}",
                    filename=path.name,
                    size=stat.st_size,
                    mime_type=guess_mime_type(path.name),
                    is_dir=False,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                    uploaded=uploads in path.parents,
                )
            )
        return entries

    def read(self, virtual_path: str) -> bytes:
        """读一个文件的内容。

        Args:
            virtual_path: 虚拟路径（如 ``/uploads/a.png``）。

        Returns:
            文件内容。

        Raises:
            ValidationError: 路径非法。
            NotFoundError: 文件不存在。
        """
        response = self._backend.download_files([virtual_path])[0]
        if response.error:
            raise _map_error(response.error, virtual_path)
        if response.content is None:
            raise NotFoundError(f"文件不存在：{virtual_path}")
        if len(response.content) > self.max_download_bytes:
            raise ValidationError(
                f"文件超过下载上限（{human_size(self.max_download_bytes)}）"
            )
        return response.content

    def download(self, virtual_path: str) -> tuple[bytes, str, str]:
        """下载一个文件，返回 (内容, 文件名, MIME 类型)。

        Args:
            virtual_path: 虚拟路径。

        Returns:
            三元组。

        Raises:
            ValidationError: 路径非法。
            NotFoundError: 文件不存在。
        """
        content = self.read(virtual_path)
        name = Path(virtual_path).name or "download"
        return content, name, guess_mime_type(name)

    def read_head(self, virtual_path: str, size: int = 32) -> bytes:
        """只读文件头。

        判断"是不是图片"用得上 —— 但没必要为了这个把整个文件读进内存。
        上传的可能是几百 MB 的日志或数据集。

        用 ``O_NOFOLLOW`` 打开：软链接一律不跟（与 ``download_files`` 一致），
        免得"检查文件头"这一步被软链接带出工作区。

        Args:
            virtual_path: 虚拟路径。
            size: 读多少字节。

        Returns:
            文件头字节；文件不存在时抛 :class:`NotFoundError`。
        """
        path = self._host_path(virtual_path)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as exc:
            raise NotFoundError(f"文件不存在：{virtual_path}") from exc
        except OSError as exc:
            raise ValidationError(f"路径不可用：{virtual_path}") from exc
        with os.fdopen(fd, "rb") as handle:
            return handle.read(size)

    def stat(self, virtual_path: str) -> FileStat:
        """取文件状态（大小、是否目录、修改时间）。

        用 ``lstat`` —— 不跟随软链接，所以软链接不会被当成普通文件。

        Args:
            virtual_path: 虚拟路径。

        Returns:
            文件状态。

        Raises:
            NotFoundError: 文件不存在。
        """
        path = self._host_path(virtual_path)
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise NotFoundError(f"文件不存在：{virtual_path}") from exc
        except OSError as exc:
            raise ValidationError(f"路径不可用：{virtual_path}") from exc
        return FileStat(
            size=info.st_size,
            is_dir=stat.S_ISDIR(info.st_mode),
            modified_at=datetime.fromtimestamp(info.st_mtime, UTC),
        )

    # ---------- 删除 ----------

    def delete(self, virtual_path: str) -> None:
        """删一个文件。

        Args:
            virtual_path: 虚拟路径。

        Raises:
            ValidationError: 路径非法。
            NotFoundError: 文件不存在。
        """
        result = self._backend.delete(virtual_path)
        if result.error:
            raise _map_error(result.error, virtual_path)

    # ---------- 写入与改名 ----------

    def write_text(self, virtual_path: str, content: str) -> int:
        """写入（覆盖）一个文本文件。

        **只写文本** —— 二进制内容由上传接口负责，在界面上编辑二进制没有意义。

        写文件时用 ``O_NOFOLLOW``：路径最后一段是软链接就拒掉，
        免得把工作区外面的文件改了。

        Args:
            virtual_path: 虚拟路径。
            content: 新的内容（UTF-8）。

        Returns:
            写进去的字节数。

        Raises:
            ValidationError: 路径非法、越界或内容太大。
        """
        data = content.encode("utf-8")
        if len(data) > self.max_upload_bytes:
            raise ValidationError(f"内容超过上限（{human_size(self.max_upload_bytes)}）")
        path = self._safe_write_path(virtual_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o644,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return len(data)

    def rename(self, virtual_path: str, new_path: str) -> str:
        """重命名（也可以是移动到别的目录）。

        Args:
            virtual_path: 原虚拟路径。
            new_path: 新虚拟路径。

        Returns:
            新路径。

        Raises:
            ValidationError: 路径非法、越界或目标已存在。
            NotFoundError: 原文件不存在。
        """
        source = self._safe_write_path(virtual_path)
        if not source.exists():
            raise NotFoundError(f"文件不存在：{virtual_path}")
        target = self._safe_write_path(new_path)
        if target.exists():
            raise ValidationError(f"目标已存在：{new_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return new_path

    # ---------- 内部 ----------

    def _safe_write_path(self, virtual_path: str) -> Path:
        """写操作用的路径解析 —— 比读更严。

        除了词法挡 ``..``，还把路径**解析后**比一下，确保落在工作区里 ——
        中间某一段是软链接指向外面的话，``resolve`` 之后就看出来了。

        Raises:
            ValidationError: 路径越界。
        """
        path = self._host_path(virtual_path)
        root = self.workspace.resolve()
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValidationError("路径越界")
        return path

    def _host_path(self, virtual_path: str) -> Path:
        """虚拟路径 → 宿主路径。

        **词法**地挡掉 ``..``：拼出来一定在工作区里面。软链接逃逸由
        ``O_NOFOLLOW``（读）与 ``FilesystemBackend``（下载/删除）各挡一层。

        Raises:
            ValidationError: 路径里有 ``..``。
        """
        raw = Path(virtual_path)
        relative = Path(*raw.parts[1:]) if raw.is_absolute() else raw
        if ".." in relative.parts:
            raise ValidationError("路径越界")
        return self.workspace / relative


def _map_error(error: str, virtual_path: str) -> AppError:
    """把后端的错误码翻译成业务异常。"""
    if error in ("file_not_found", "not_found"):
        return NotFoundError(f"文件不存在：{virtual_path}")
    if error in ("invalid_path", "permission_denied", "is_directory"):
        return ValidationError(f"路径不可用：{virtual_path}（{error}）")
    return AppError(f"文件操作失败：{error}")


__all__ = [
    "UPLOADS_DIR",
    "FileEntry",
    "FileService",
    "FileStat",
    "UploadResult",
]
