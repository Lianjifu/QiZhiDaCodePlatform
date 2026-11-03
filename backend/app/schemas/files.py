"""文件接口的请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class FileOut(BaseModel):
    """工作区里的一个文件。"""

    path: str = Field(description="虚拟路径，以 / 开头，以工作区为根")
    filename: str = Field(description="文件名")
    size: int = Field(default=0, description="字节数")
    mime_type: str = Field(default="", description="MIME 类型")
    is_dir: bool = Field(default=False, description="是不是目录")
    modified_at: datetime | None = Field(default=None, description="最后修改时间")
    uploaded: bool = Field(default=False, description="是不是用户上传件")


class UploadOut(BaseModel):
    """一次上传的结果。"""

    path: str = Field(description="虚拟路径；把它带进对话就能让 agent 处理")
    filename: str = Field(description="落盘后的文件名（带时间戳前缀）")
    original_name: str = Field(default="", description="用户传时的原始文件名")
    size: int = Field(default=0, description="字节数")
    sha256: str = Field(default="", description="内容哈希")
    mime_type: str = Field(default="", description="MIME 类型")
    is_image: bool = Field(default=False, description="是不是图片")
    uploaded_at: datetime = Field(description="上传时间")


class FileListOut(BaseModel):
    """工作区文件列表。"""

    files: list[FileOut] = Field(default_factory=list, description="文件清单")
    total: int = Field(default=0, description="文件总数")


class FileWriteIn(BaseModel):
    """保存文件内容（覆盖写）。"""

    content: str = Field(default="", description="新的文件内容（UTF-8 文本）")


class FileRenameIn(BaseModel):
    """重命名 / 移动。"""

    new_path: str = Field(min_length=1, max_length=512, description="新的虚拟路径")


__all__ = ["FileListOut", "FileOut", "FileRenameIn", "FileWriteIn", "UploadOut"]
