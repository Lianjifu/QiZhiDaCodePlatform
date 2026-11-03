"""文件接口：上传 / 列表 / 下载 / 删除 / 写入 / 重命名。

用户要给 agent 材料（需求文档、报错截图、代码片段），也要把 agent 做出来的
东西拿走 —— 这组接口是两头。

## 安全提醒

**平台目前没有鉴权**（见记忆里的 `no-auth-for-now`）。这意味着：

- 知道 ``thread_id`` 就能下载这个会话里的**所有文件**（含用户的需求文档）；
- 也能往别人的会话里塞文件；
- **还能改** —— 写入与重命名同样是裸接口，知道 ``thread_id`` 就能覆盖别人
  工作区里的文件；
- ``thread_id`` 由客户端生成，泄露即等于交出整个工作区。

这是"记忆接口无鉴权"那条风险的更严重版本 —— 那里泄露的是用户偏好，这里是
用户的文件。上线前必须补鉴权。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError
from app.core.workspace import resolve_workspace
from app.files.service import FileService
from app.files.storage import sanitize_filename
from app.schemas.common import ResponseModel
from app.schemas.files import FileListOut, FileOut, FileRenameIn, FileWriteIn, UploadOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["文件"])


def _service(thread_id: str) -> FileService:
    """按会话取文件服务。"""
    workspace = resolve_workspace(thread_id)
    return FileService(
        workspace,
        max_upload_bytes=settings.max_upload_bytes,
        max_download_bytes=settings.max_download_bytes,
    )


@router.post(
    "",
    response_model=ResponseModel[UploadOut],
    summary="上传文件到会话工作区",
)
async def upload_file(
    thread_id: Annotated[str, Query(description="会话 ID")],
    file: Annotated[UploadFile, File(description="文件本体")],
) -> ResponseModel[UploadOut]:
    """上传一个文件。

    文件落进会话工作区的 ``uploads/`` —— agent 用现成的文件工具就能读，
    不需要新增任何 agent 能力。

    返回的 ``path`` 是**虚拟路径**（``/uploads/xxx.png``）。把它带进对话
    （``attachments``）就能让 agent 处理这个文件。
    """
    content = await file.read()
    service = _service(thread_id)
    result = service.upload(file.filename or "", content, mime_type=file.content_type)
    return ResponseModel[UploadOut](
        data=UploadOut(
            path=result.path,
            filename=result.filename,
            original_name=sanitize_filename(file.filename or ""),
            size=result.size,
            sha256=result.sha256,
            mime_type=result.mime_type,
            is_image=result.is_image,
            uploaded_at=result.uploaded_at,
        )
    )


@router.get("", response_model=ResponseModel[FileListOut], summary="列出工作区文件")
async def list_files(
    thread_id: Annotated[str, Query(description="会话 ID")],
    include_uploads: Annotated[bool, Query(description="是否列出上传件")] = True,
) -> ResponseModel[FileListOut]:
    """列出工作区里的文件（上传件 + agent 的产物）。

    **不含平台产物** —— 那是工作区外面的 ``.artifacts/``，里面有被压缩掉的
    对话历史，不给用户看也不给下载。
    """
    service = _service(thread_id)
    entries = service.list_files(include_uploads=include_uploads)
    return ResponseModel[FileListOut](
        data=FileListOut(
            files=[
                FileOut(
                    path=entry.path,
                    filename=entry.filename,
                    size=entry.size,
                    mime_type=entry.mime_type,
                    is_dir=entry.is_dir,
                    modified_at=entry.modified_at,
                    uploaded=entry.uploaded,
                )
                for entry in entries
            ],
            total=len(entries),
        )
    )


@router.get("/tree", response_model=ResponseModel[FileListOut], summary="列出工作区文件与目录")
async def list_tree(
    thread_id: Annotated[str, Query(description="会话 ID")],
) -> ResponseModel[FileListOut]:
    """工作区的**目录树**（文件 + 目录）。

    @ 提及面板要用 —— 那边得能选目录，光有文件不够。
    """
    service = _service(thread_id)
    entries = service.list_files(include_uploads=True, include_dirs=True)
    return ResponseModel[FileListOut](
        data=FileListOut(
            files=[
                FileOut(
                    path=entry.path,
                    filename=entry.filename,
                    size=entry.size,
                    mime_type=entry.mime_type,
                    is_dir=entry.is_dir,
                    modified_at=entry.modified_at,
                    uploaded=entry.uploaded,
                )
                for entry in entries
            ],
            total=len(entries),
        )
    )


@router.get("/archive", summary="打包下载工作区文件")
async def download_archive(
    thread_id: Annotated[str, Query(description="会话 ID")],
) -> FileResponse:
    """把工作区打成一个 zip 下载。

    **为什么需要**：agent 改完一个项目有几十个文件，用户要的是"打包下载"，
    不是一个一个点。

    zip **先写到临时文件**再回传 —— 工作区可能有几百 MB，边生成边吐会让
    整个包驻留内存。传完就删。

    单个文件超上限会跳过（不影响整包），跳过了哪些会记日志。
    """
    service = _service(thread_id)
    target = Path(tempfile.mkstemp(prefix="qzd-archive-", suffix=".zip")[1])
    try:
        count, skipped = service.build_archive(target)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise AppError(f"打包失败：{exc}") from exc

    for note in skipped:
        logger.warning("打包时跳过 %s", note)
    if count == 0:
        target.unlink(missing_ok=True)
        raise NotFoundError("工作区里没有可下载的文件")

    return FileResponse(
        target,
        media_type="application/zip",
        filename=f"{_safe_name(thread_id)}.zip",
        # 传完删临时文件
        background=BackgroundTask(target.unlink, missing_ok=True),
    )


def _safe_name(thread_id: str) -> str:
    """给下载文件名用 —— 别把用户输入原样塞进 header。"""
    cleaned = "".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")[:40]
    return cleaned or "workspace"


@router.get("/download", summary="下载工作区文件")
async def download_file(
    thread_id: Annotated[str, Query(description="会话 ID")],
    path: Annotated[str, Query(description="虚拟路径，如 /uploads/a.png")],
) -> Response:
    """按虚拟路径下载文件。

    路径解析走 ``FilesystemBackend`` 的虚拟模式 —— ``..`` 穿越、软链接逃逸
    都会被挡掉，挡不住时返回 400 而不是 500。

    响应头带 ``Content-Disposition: attachment`` —— 免得浏览器把用户上传的
    HTML/SVG 当页面渲染（存储型 XSS）。
    """
    service = _service(thread_id)
    content, filename, mime_type = service.download(path)
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": _disposition(filename)},
    )


@router.delete("", summary="删除工作区文件")
async def delete_file(
    thread_id: Annotated[str, Query(description="会话 ID")],
    path: Annotated[str, Query(description="虚拟路径")],
) -> ResponseModel[dict[str, str]]:
    """删一个文件。"""
    service = _service(thread_id)
    service.delete(path)
    return ResponseModel[dict[str, str]](data={"path": path, "status": "deleted"})


@router.put("", summary="保存工作区文件内容")
async def write_file(
    thread_id: Annotated[str, Query(description="会话 ID")],
    path: Annotated[str, Query(description="虚拟路径")],
    payload: FileWriteIn,
) -> ResponseModel[dict[str, str]]:
    """写入（覆盖）一个文本文件。

    只处理文本 —— 二进制内容走上传接口。界面上"改文件"就是这条。

    路径越界（软链接指向工作区外面）会被挡掉。
    """
    service = _service(thread_id)
    size = service.write_text(path, payload.content)
    return ResponseModel[dict[str, str]](
        data={"path": path, "status": "saved", "size": str(size)}
    )


@router.patch("", summary="重命名工作区文件")
async def rename_file(
    thread_id: Annotated[str, Query(description="会话 ID")],
    path: Annotated[str, Query(description="虚拟路径")],
    payload: FileRenameIn,
) -> ResponseModel[dict[str, str]]:
    """重命名（或移动）一个文件。"""
    service = _service(thread_id)
    new_path = service.rename(path, payload.new_path)
    return ResponseModel[dict[str, str]](data={"path": new_path, "status": "renamed"})


def _disposition(filename: str) -> str:
    """拼 Content-Disposition。

    非 ASCII 文件名要按 RFC 5987 编码 —— 直接塞进 header 会被某些客户端解析坏。
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "download"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


__all__ = ["router"]
