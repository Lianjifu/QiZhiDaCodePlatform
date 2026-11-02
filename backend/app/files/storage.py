"""文件存储的布局与路径规则。

## 三类文件

| | 上传件 | 工作产物 | 平台产物 |
| --- | --- | --- | --- |
| 谁产生 | 用户 | agent | 框架（deepAgents） |
| 存在哪 | 工作区 ``uploads/`` | 工作区（agent 写哪就在哪） | 工作区**外面**的 ``.artifacts/`` |
| agent 看得见 | ✅ | ✅ | ❌ |
| 用户能下载 | ✅ | ✅ | ❌ |

平台产物不给下载 —— 那里面有被压缩掉的对话历史（隐私内容）。

## 路径一律是虚拟路径

对外暴露的都是以 ``/`` 开头、以工作区为根的虚拟路径（``/uploads/xxx.png``）。
宿主路径只在拼命令时用。**路径安全由 deepAgents 的 ``FilesystemBackend``
（``virtual_mode=True``）保证** —— ``..`` 穿越、``~``、软链接逃逸都会被它挡掉，
我们不用再写一遍路径检查。
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# 上传件目录（相对工作区）
UPLOADS_DIRNAME = "uploads"

# 上传件目录的虚拟路径前缀
UPLOADS_ROUTE = f"/{UPLOADS_DIRNAME}"

# 能内联进对话、让模型直接"看见"的图片类型。
#
# **SVG 不在内**：模型端普遍不收 SVG，而且它是脚本载体 —— 当图片内联没有必要，
# 当文件下载又要防 XSS（靠下载接口的 Content-Disposition）。
INLINE_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/bmp",
    }
)

# 文件名里要洗掉的字符（控制字符、路径分隔符、shell 特殊字符）
_UNSAFE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")
# 连续的点（防 ".." 与 "..."）
_DOT_RUN = re.compile(r"\.{2,}")


def sanitize_filename(name: str) -> str:
    """把上传时的文件名洗成安全的文件名。

    去掉目录部分（``a/b/c.png`` → ``c.png``）、控制字符、路径分隔符，
    并把连续的点压掉 —— 留着 ``..`` 就可能拼出穿越路径。

    Args:
        name: 客户端给的文件名。

    Returns:
        安全的文件名（可能为空 —— 调用方要自己兜底）。
    """
    base = name.replace("\\", "/").split("/")[-1]
    base = _UNSAFE_NAME_CHARS.sub("_", base).strip()
    base = _DOT_RUN.sub(".", base).strip(". ")
    if base.lower().endswith("."):
        base = base[:-1]
    return base[:180]


def unique_filename(
    name: str,
    *,
    now: datetime | None = None,
    exists: Callable[[str], bool] | None = None,
) -> str:
    """给上传件起一个不重名的文件名。

    带时间戳前缀 —— 同一个会话里用户可能传两次同名文件，不加前缀会互相覆盖，
    agent 也可能读到"上次那个"。

    时间戳只到秒，**同一秒内传两次同名文件还是会撞**。所以给了 ``exists``
    回调时会在名字后面补序号（``..._1``、``..._2``），保证不覆盖。

    Args:
        name: 原始文件名。
        now: 时间戳来源（测试可注入）。
        exists: 判断某个文件名是否已被占用。

    Returns:
        文件名（不含目录）。
    """
    safe = sanitize_filename(name) or "file"
    stem, suffix = _split_suffix(safe)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d_%H%M%S")
    candidate = f"{stamp}_{stem}{suffix}"
    if exists is None:
        return candidate
    index = 1
    while exists(candidate):
        candidate = f"{stamp}_{stem}_{index}{suffix}"
        index += 1
    return candidate


def _split_suffix(name: str) -> tuple[str, str]:
    """拆成 (主干, 后缀)。后缀最长 10 个字符 —— 防止 ".tar.gz" 之类把主干掏空。"""
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    suffix = name[dot:]
    if len(suffix) > 10:
        return name, ""
    return name[:dot], suffix


def uploads_dir(workspace: Path) -> Path:
    """工作区里的上传件目录。"""
    return Path(workspace) / UPLOADS_DIRNAME


def guess_mime_type(filename: str) -> str:
    """按文件名猜 MIME 类型；猜不到就是通用二进制。"""
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def is_image(mime_type: str | None, filename: str = "") -> bool:
    """是不是图片（按 MIME，猜不到就按文件名）。"""
    mime = mime_type or guess_mime_type(filename)
    return mime.lower().startswith("image/")


def is_inline_image(mime_type: str | None, filename: str = "") -> bool:
    """能不能内联进对话让模型直接看。"""
    mime = (mime_type or guess_mime_type(filename)).lower()
    return mime in INLINE_IMAGE_MIME_TYPES


def human_size(size: int) -> str:
    """给人看的体积（给模型看的上下文里用）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


__all__ = [
    "INLINE_IMAGE_MIME_TYPES",
    "UPLOADS_DIRNAME",
    "UPLOADS_ROUTE",
    "guess_mime_type",
    "human_size",
    "is_image",
    "is_inline_image",
    "sanitize_filename",
    "unique_filename",
    "uploads_dir",
]
