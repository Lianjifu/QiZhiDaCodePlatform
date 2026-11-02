"""文件模块：工作区里的上传件与产物。

对外只有 ``/api/v1/files`` 一组接口；agent 侧不用学新能力 —— 上传件落在
工作区里，现成的 ``read_file`` / ``glob`` / ``grep`` 就能处理。
"""

from app.files.service import FileEntry, FileService, UploadResult
from app.files.storage import (
    UPLOADS_ROUTE,
    guess_mime_type,
    human_size,
    is_image,
    is_inline_image,
    sanitize_filename,
    unique_filename,
    uploads_dir,
)

__all__ = [
    "UPLOADS_ROUTE",
    "FileEntry",
    "FileService",
    "UploadResult",
    "guess_mime_type",
    "human_size",
    "is_image",
    "is_inline_image",
    "sanitize_filename",
    "unique_filename",
    "uploads_dir",
]
