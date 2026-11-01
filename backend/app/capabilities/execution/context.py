"""执行上下文。

执行层需要知道"在哪儿干活""沙箱怎么配""哪些 MCP server 可用"。
这些是运行时信息，由编排层组装好后传给执行层 —— **不会出现在给 LLM 的
tool schema 里**。

**没有"当前目录"这回事**：deepAgents 接管文件工具后，所有路径一律相对工作区
根目录，会话级 ``cd`` 随之消失（``cd`` 的等价物是命令里的 ``cd``，而 ``execute``
本来就在工作区根目录起）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.models.mcp_server import MCPServer

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """一次执行的环境。

    执行层所有需要"外部世界"的信息都收在这里：能力要知道工作区，
    执行类能力要知道沙箱怎么配，MCP 能力要知道连哪些 server，
    留痕要知道往哪个数据库会话写。
    """

    workspace: Path  # 工作区根目录，所有路径的边界
    mcp_servers: dict[str, MCPServer] = field(default_factory=dict)  # 本次可用的 MCP 供给通道
    max_file_bytes: int = 200000  # 单个文件读入的上限
    sandbox_type: str = "local"  # 沙箱模式：local / docker
    sandbox_image: str = "python:3.13-slim"  # docker 模式用的镜像
    sandbox_timeout: int = 60  # 沙箱内单次执行的超时（秒）
    docker_mount: str = "/workspace"  # 工作区在容器里的挂载点
    # 数据库会话：有会话才写调用记录（执行层统一留痕的落点）
    session: AsyncSession | None = None
    # 会话 ID：调用记录按它归集，幂等键也带上它
    thread_id: str = ""

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)

    @classmethod
    def build(
        cls,
        workspace: str | Path | None = None,
        mcp_servers: dict[str, MCPServer] | None = None,
    ) -> ExecutionContext:
        """按配置构造上下文。

        Args:
            workspace: 工作区根目录，不传则用配置里的 ``workspace_root``。
            mcp_servers: 本次可用的 MCP server。

        Returns:
            构造好的执行上下文。
        """
        return cls(
            workspace=Path(workspace or settings.workspace_root),
            mcp_servers=dict(mcp_servers or {}),
            max_file_bytes=settings.agent_max_file_bytes,
            sandbox_type=settings.sandbox_type,
            sandbox_image=settings.sandbox_image,
            sandbox_timeout=settings.sandbox_timeout,
            docker_mount=settings.agent_docker_mount,
        )

    @property
    def workdir(self) -> Path:
        """工作目录（= 工作区根）。

        保留这个属性名是为了少改调用点；语义上它就是工作区根，没有别的了。
        """
        return self.workspace
