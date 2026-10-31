"""内置工具的运行时上下文。

内置工具的工厂函数需要知道"在哪个工作区里干活""沙箱怎么配"。
这些参数由运行时注入，不会出现在给 LLM 的 tool schema 里。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def build_runtime_context(workspace: str | Path | None = None) -> dict:
    """构造内置工具的运行时上下文。

    Args:
        workspace: 本次会话的工作区；不传则用配置里的默认工作区根目录。
    """
    return {
        "workspace": str(workspace or settings.workspace_root),
        "max_file_bytes": settings.agent_max_file_bytes,
        "sandbox_type": settings.sandbox_type,
        "sandbox_image": settings.sandbox_image,
        "sandbox_timeout": settings.sandbox_timeout,
    }
