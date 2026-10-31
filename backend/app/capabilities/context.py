"""执行上下文。

执行层需要知道"在哪儿干活""沙箱怎么配""哪些 MCP server 可用"。
这些是运行时信息，由编排层组装好后传给执行层 —— **不会出现在给 LLM 的
tool schema 里**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.models.mcp_server import MCPServer


@dataclass
class ExecutionContext:
    """一次执行的环境。"""

    workspace: Path
    mcp_servers: dict[str, MCPServer] = field(default_factory=dict)
    max_file_bytes: int = 200000
    sandbox_type: str = "local"
    sandbox_image: str = "python:3.13-slim"
    sandbox_timeout: int = 60
    docker_mount: str = "/workspace"

    @classmethod
    def build(
        cls,
        workspace: str | Path | None = None,
        mcp_servers: dict[str, MCPServer] | None = None,
    ) -> ExecutionContext:
        """按配置构造上下文。"""
        return cls(
            workspace=Path(workspace or settings.workspace_root),
            mcp_servers=dict(mcp_servers or {}),
            max_file_bytes=settings.agent_max_file_bytes,
            sandbox_type=settings.sandbox_type,
            sandbox_image=settings.sandbox_image,
            sandbox_timeout=settings.sandbox_timeout,
            docker_mount=settings.agent_docker_mount,
        )
