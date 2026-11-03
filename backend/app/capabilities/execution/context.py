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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.core.locking import TaskLock
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
    # 会话自己的目录（上传件、技能导出、产物、文件页看的东西）。
    #
    # **绑了工作目录之后它和 ``workspace`` 不是同一个地方**：``workspace``
    # 变成用户绑的环境（可能是真实的代码仓库），上传件不能落进仓库里 ——
    # 所以上传件留在这里，并作为 ``/uploads`` 路由挂给 agent。
    #
    # 不给时等同于 ``workspace``（也就是没绑环境的原有行为）。
    session_workspace: Path | None = None
    mcp_servers: dict[str, MCPServer] = field(default_factory=dict)  # 本次可用的 MCP 供给通道
    max_file_bytes: int = 200000  # 单个文件读入的上限
    sandbox_type: str = "docker"  # 沙箱模式：docker / local
    sandbox_image: str = "python:3.13-slim"  # docker 模式用的镜像
    sandbox_timeout: int = 60  # 沙箱内单次执行的超时（秒）
    docker_mount: str = "/workspace"  # 工作区在容器里的挂载点
    # 数据库会话：有会话才写调用记录（执行层统一留痕的落点）
    session: AsyncSession | None = None
    # 会话 ID：调用记录按它归集，幂等键也带上它
    thread_id: str = ""
    # 本次可用的能力清单。
    #
    # **给派子 agent 用**（见 app/agents/worker.py）：worker 要有一份自己的
    # 工具清单，而能力实现只拿得到 ExecutionContext —— 清单就放在这儿。
    specs: list[Any] = field(default_factory=list)
    # 本次用的模型对象。
    #
    # 同样是为了 worker：worker 用**同一个模型**（用户在界面上选的那个
    # 供应商），不能退回环境变量里的默认配置。
    model: Any = None
    # **审批已经由上层处理过了** —— 执行层不要再问一遍。
    #
    # agent 路径：审批走 deepAgents 的 HITL，工具能跑到执行层就说明人已经批过了
    # （或者这个工具压根不需要批）。执行层再判一次，等于批准了又被拦下。
    #
    # 直接调用的路径：不设这个标志，执行层自己决定要不要挂起等人批。
    approval_handled: bool = False

    # 数据库会话的锁。
    #
    # **工具是并行跑的**（LangGraph 的 ToolNode 并发执行同一次回复里的多个
    # 调用），而 SQLAlchemy 的 AsyncSession 不允许并发使用 —— 两个调用同时
    # flush 会炸（"Session is already flushing"）。
    #
    # 锁只护住**数据库操作**（查、写、flush），不护工具本身的执行 ——
    # 沙箱里跑命令那几十秒不能被锁住，不然并行就白并行了。
    #
    # 用可重入的锁：记忆的写入会层层调用（写 → 取集合 id → 写），
    # 不可重入的锁在那里会死锁。
    db_lock: TaskLock = field(default_factory=TaskLock)

    @asynccontextmanager
    async def db(self) -> AsyncIterator[None]:
        """在锁里做数据库操作。

        用法::

            async with context.db():
                await session.flush()

        **别把锁持到别的地方去** —— 锁不可重入，嵌套会死锁。
        """
        async with self.db_lock:
            yield

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        if self.session_workspace is None:
            self.session_workspace = self.workspace
        else:
            self.session_workspace = Path(self.session_workspace)

    @classmethod
    def build(
        cls,
        workspace: str | Path | None = None,
        mcp_servers: dict[str, MCPServer] | None = None,
        session_workspace: str | Path | None = None,
    ) -> ExecutionContext:
        """按配置构造上下文。

        Args:
            workspace: agent 的工作根目录（绑了环境就是环境目录）。
            mcp_servers: 本次可用的 MCP server。
            session_workspace: 会话自己的目录；不给就等同于 ``workspace``。

        Returns:
            构造好的执行上下文。
        """
        return cls(
            workspace=Path(workspace or settings.workspace_root),
            session_workspace=(
                Path(session_workspace) if session_workspace is not None else None
            ),
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
