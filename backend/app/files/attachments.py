"""把附件变成模型能用的东西。

用户上传的附件分两种处理：

| 附件 | 怎么处理 | 为什么 |
| --- | --- | --- |
| 图片 | **内联**进用户消息（``image_url``），模型这一轮就能"看见" | 见下 |
| 其它文件 | 只给**路径**，写进上下文的「上传件」块 | 模型看不见 pdf/docx，只能让 agent 自己读 |

**为什么图片不靠 agent 读文件来看**：``read_file`` 对图片返回的是 ``tool``
角色消息里带图片块，而 OpenAI 协议里 tool 消息只该有文本，兼容端点会拒。
内联是**这一轮就生效**，也不用多一次工具往返。

实测 DeepSeek 能读内联图片（能说出图里左右分别是红蓝），所以这条路是通的。

## 为什么图片还要落盘

图片**同时**落进工作区的 ``uploads/``：模型这一轮看得见它，用户也下载得到，
agent 想做像素级处理（裁剪、转格式、OCR）时还能用 ``execute``。

## 不整份读文件

判断"是不是图片"只看**文件头**（32 字节），大小看 ``stat``。上传的可能是
几百 MB 的日志或数据集 —— 为了判个类型把整份读进内存不值得。

## 内联的三道闸

1. **模型能不能看图** —— 供应商配置上的 ``vision_enabled``。不能看图就不内联，
   降级成只给路径，而不是让整轮对话报错。
2. **图够不够大** —— base64 内联会让 checkpoint 明显膨胀。超限就只给路径。
3. **它真的是图片吗** —— 按文件头（magic bytes）判断，不信扩展名。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from app.files.service import FileService
from app.files.storage import guess_mime_type, human_size, is_inline_image

# 文件头 → MIME。只认真正能内联的格式。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image_mime(data: bytes) -> str | None:
    """按文件头判断是不是图片，是就返回 MIME。

    扩展名会骗人 —— 用户把文本改名成 .png，内联过去模型会直接报错。
    WebP 的文件头在 RIFF 容器里，单独判断。
    """
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class Attachment:
    """一个附件的解析结果。"""

    path: str
    filename: str
    size: int
    mime_type: str
    is_image: bool
    inlined: bool = False
    reason: str = ""


def build_attachments(
    workspace: Path,
    paths: list[str],
    *,
    vision_enabled: bool,
    max_inline_bytes: int,
    max_attachments: int = 10,
) -> tuple[list[dict], list[Attachment]]:
    """解析附件清单，返回 (内联的图片内容块, 附件明细)。

    Args:
        workspace: 会话工作区。
        paths: 附件的虚拟路径列表。
        vision_enabled: 当前模型能不能看图。
        max_inline_bytes: 单张图片内联的大小上限。
        max_attachments: 一轮最多几个附件。

    Returns:
        二元组。内容块是 LangChain 的标准块
        ``{"type": "image", "base64": ..., "mime_type": ...}`` —— 供应商侧
        会转成 ``image_url``。
    """
    service = FileService(workspace)
    blocks: list[dict] = []
    resolved: list[Attachment] = []
    for path in paths[:max_attachments]:
        try:
            info = service.stat(path)
            head = service.read_head(path, 32)
        except Exception:
            # 附件读不到不该让整轮对话失败 —— 跳过它，agent 那边路径清单里
            # 也不会有它
            continue
        filename = Path(path).name
        mime = sniff_image_mime(head)
        is_image = mime is not None and is_inline_image(mime, filename)
        if not is_image:
            resolved.append(
                Attachment(
                    path=path,
                    filename=filename,
                    size=info.size,
                    mime_type=guess_mime_type(filename),
                    is_image=False,
                )
            )
            continue
        if not vision_enabled:
            resolved.append(
                Attachment(
                    path=path,
                    filename=filename,
                    size=info.size,
                    mime_type=mime,
                    is_image=True,
                    reason="当前模型不支持看图，只给了路径",
                )
            )
            continue
        if info.size > max_inline_bytes:
            resolved.append(
                Attachment(
                    path=path,
                    filename=filename,
                    size=info.size,
                    mime_type=mime,
                    is_image=True,
                    reason=f"图片超过 {human_size(max_inline_bytes)}，只给了路径",
                )
            )
            continue
        content = service.read(path)
        blocks.append(
            {
                "type": "image",
                "base64": base64.b64encode(content).decode("ascii"),
                "mime_type": mime,
            }
        )
        resolved.append(
            Attachment(
                path=path,
                filename=filename,
                size=len(content),
                mime_type=mime,
                is_image=True,
                inlined=True,
            )
        )
    return blocks, resolved


def build_uploads_note(
    workspace: Path,
    *,
    attachments: list[Attachment] | None = None,
    max_entries: int = 20,
) -> str:
    """渲染上下文里的「上传件」块。

    列的是**工作区里全部的上传件**（按修改时间倒序），不只是本轮带的 ——
    用户上传完隔两轮再问"刚才那个文件"，agent 照样找得到。

    Args:
        workspace: 会话工作区。
        attachments: 本轮解析出的附件（用来标注哪些已内联）。
        max_entries: 最多列几个。

    Returns:
        XML 片段；没有上传件时返回空串。
    """
    from app.files.storage import uploads_dir

    directory = uploads_dir(workspace)
    if not directory.exists():
        return ""

    files = [p for p in directory.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:max_entries]
    if not files:
        return ""

    inlined = {a.path for a in (attachments or []) if a.inlined}
    lines = [
        "<uploaded_files>",
        "工作区里有用户上传的材料。需要时用 read_file 读取"
        "（路径是虚拟路径，以 / 为工作区根）：",
    ]
    for path in files:
        virtual = f"/uploads/{path.name}"
        size = path.stat().st_size
        if virtual in inlined:
            note = "已随本条消息附上，可直接查看"
        else:
            note = ""
        suffix = f"（{human_size(size)}{'，' + note if note else ''}）"
        lines.append(f"- {virtual} {suffix}")
    lines.append("</uploaded_files>")
    return "\n".join(lines)


__all__ = [
    "Attachment",
    "build_attachments",
    "build_uploads_note",
    "sniff_image_mime",
]
