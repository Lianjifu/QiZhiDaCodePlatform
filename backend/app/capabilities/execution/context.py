"""执行上下文。

执行层需要知道"在哪儿干活""沙箱怎么配""哪些 MCP server 可用"。
这些是运行时信息，由编排层组装好后传给执行层 —— **不会出现在给 LLM 的
tool schema 里**。

关于"当前目录"（cwd）：文件类能力和执行类能力都以它为相对路径的起点，
`change_directory` 能力会改它。同一会话的多次请求之间要保持，所以落在
工作区**外面**的一个状态文件里 —— 放在工作区里面的话，模型的文件工具
会看见它，工作区也会被状态文件污染。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.models.mcp_server import MCPServer

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 状态文件目录（放在工作区的父目录下，模型看不见）
_STATE_DIR = ".qzd_state"


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
    # 当前工作目录：相对路径的起点，默认等于工作区根目录
    cwd: Path | None = None
    # 数据库会话：有会话才写调用记录（执行层统一留痕的落点）
    session: AsyncSession | None = None
    # 会话 ID：调用记录按它归集，幂等键也带上它
    thread_id: str = ""

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.cwd = Path(self.cwd) if self.cwd is not None else self.workspace

    @classmethod
    def build(
        cls,
        workspace: str | Path | None = None,
        mcp_servers: dict[str, MCPServer] | None = None,
        restore_cwd: bool = True,
    ) -> ExecutionContext:
        """按配置构造上下文。

        `restore_cwd` 为真时会读回上一次 `cd` 留下的位置（同一会话）。
        """
        context = cls(
            workspace=Path(workspace or settings.workspace_root),
            mcp_servers=dict(mcp_servers or {}),
            max_file_bytes=settings.agent_max_file_bytes,
            sandbox_type=settings.sandbox_type,
            sandbox_image=settings.sandbox_image,
            sandbox_timeout=settings.sandbox_timeout,
            docker_mount=settings.agent_docker_mount,
        )
        if restore_cwd:
            context.restore_cwd()
        return context

    # ---------- 当前目录 ----------

    @property
    def workdir(self) -> Path:
        """当前工作目录。文件类与执行类能力都以它为相对路径起点。"""
        return self.cwd or self.workspace

    def set_cwd(self, path: Path) -> None:
        """切换当前目录，并记住它（跨请求保持）。"""
        self.cwd = Path(path)
        self._save_cwd()

    # ---------- cwd 的持久化 ----------

    @property
    def _state_file(self) -> Path | None:
        """状态文件路径：工作区的父目录下的 `.qzd_state/<工作区名>.json`。

        放在工作区外面有两个原因：模型的文件工具只看得到工作区内的东西；
        工作区也不该被状态文件污染。
        """
        try:
            parent = self.workspace.resolve().parent
        except OSError:  # pragma: no cover - 路径异常
            return None
        return parent / _STATE_DIR / f"{self.workspace.name}.json"

    def restore_cwd(self) -> None:
        """读回上次 `cd` 的位置。状态文件丢失或目录已被删除时退回工作区根目录。"""
        state_file = self._state_file
        if state_file is None or not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            relative = data.get("cwd") or ""
        except (OSError, ValueError, AttributeError):
            return
        if not relative:
            return
        candidate = (self.workspace.resolve() / relative).resolve()
        root = self.workspace.resolve()
        if candidate != root and root not in candidate.parents:
            return
        if candidate.is_dir():
            self.cwd = candidate

    def _save_cwd(self) -> None:
        state_file = self._state_file
        if state_file is None:
            return
        try:
            root = self.workspace.resolve()
            current = self.workdir.resolve()
            relative = str(current.relative_to(root))
        except (OSError, ValueError):
            return
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps({"cwd": relative}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - 磁盘问题
            logger.debug("保存当前目录失败: %s", exc)
