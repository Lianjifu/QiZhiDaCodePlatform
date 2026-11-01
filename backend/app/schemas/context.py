"""上下文预览接口的响应模型。

**为什么要有预览**：拼装结果看不见就没法排查。"为什么模型没记住这条记忆"、
"为什么提示词里没有工作区说明"——这些问题只能看着实际拼出来的提示词才答得上来。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextBlockOut(BaseModel):
    """一个上下文块的诊断信息。"""

    key: str = Field(description="块的标识")
    title: str = Field(default="", description="块标题")
    priority: int = Field(default=0, description="优先级，数字越小越先装")
    chars: int = Field(default=0, description="占用的字符数")
    truncated: bool = Field(default=False, description="是否被截断")


class ContextPreviewOut(BaseModel):
    """一次上下文装配的预览。"""

    text: str = Field(default="", description="拼好的提示词正文")
    budget_chars: int = Field(default=0, description="本次的字符预算")
    used_chars: int = Field(default=0, description="实际用掉的字符")
    included: list[ContextBlockOut] = Field(default_factory=list, description="装进去的块")
    dropped: list[ContextBlockOut] = Field(default_factory=list, description="被丢掉的块")
    truncated: list[ContextBlockOut] = Field(default_factory=list, description="被截断的块")
    warnings: list[str] = Field(default_factory=list, description="需要注意的地方")


__all__ = ["ContextBlockOut", "ContextPreviewOut"]
