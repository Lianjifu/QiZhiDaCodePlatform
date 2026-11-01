"""后端组装：文件操作本地、命令执行进沙箱。

deepAgents 的 ``CompositeBackend`` 按**路径前缀**路由文件操作，而**命令执行
不可按路径路由，固定走 default backend** —— 正好对上我们要的组合：

- 文件操作（读 / 写 / 改 / 搜 / 删）→ ``FilesystemBackend(root_dir=工作区)``：
  本地执行，快；``virtual_mode=True`` 会阻断 ``..`` / ``~``，并要求 resolve 后
  仍在 root 内 —— 路径安全由它保证，我们不用再写一遍。
- 命令执行 → 我们的 ``DockerSandbox``：隔离参数原样保留。

一个必须成立的一致性：容器只挂载会话工作区，所以容器里写的文件落在同一个
工作区目录，``execute`` 造出的文件随后能被文件工具读到。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend

from app.capabilities.backends.platform_backend import PlatformBackend
from app.capabilities.backends.sandbox_backend import DockerSandbox
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.skills_export import SKILLS_ROUTE

# 产物目录的路由前缀（对话历史外置等）。挂在工作区**外面**。
ARTIFACTS_ROUTE = "/.artifacts/"


def build_backend(context: ExecutionContext, skills_dir: Path | None = None) -> PlatformBackend:
    """按执行上下文组装后端。

    Args:
        context: 执行上下文（工作区、沙箱配置）。
        skills_dir: 技能导出目录。给了就挂一条 ``/skills/`` 路由 ——
            deepAgents 的技能中间件按虚拟路径读它。

    Returns:
        组装好的后端：文件操作本地、命令执行进沙箱。

    外面包了一层 ``PlatformBackend`` —— 用来补 ``ls("/")`` 看不到子路由的
    问题（见 ``platform_backend.py``）。

    沙箱不可用时（没有 docker）不静默降级 —— 宁可让 agent 直接失败，
    也不要让它在没有隔离的环境里执行命令。调用方（agent 组装）负责处理这个
    异常并给出可读的提示。
    """
    workspace = context.workspace
    default = _sandbox_backend(context, workspace)
    routes: dict[str, Any] = {"/": FilesystemBackend(root_dir=workspace)}
    if skills_dir is not None:
        # 技能目录挂在工作区外面：agent 的文件工具只看得见工作区里的东西，
        # 改不到这些导出文件。
        routes[SKILLS_ROUTE] = FilesystemBackend(root_dir=skills_dir)
    # 产物目录（对话历史外置）同样挂在工作区外面 —— 见 _artifacts_root。
    routes[ARTIFACTS_ROUTE] = FilesystemBackend(root_dir=_artifacts_dir(context))
    # 包一层：补上 `ls("/")` 看不到子路由的问题（见 platform_backend.py）
    return PlatformBackend(
        CompositeBackend(
            default=default,
            routes=routes,
            artifacts_root=_artifacts_root(context),
        ),
        routes=routes,
    )


def _artifacts_dir(context: ExecutionContext) -> Path:
    """产物目录在宿主上的位置（工作区外面）。"""
    return Path(context.workspace).parent / ".artifacts" / Path(context.workspace).name


def _artifacts_root(context: ExecutionContext) -> str:
    """产物目录（对话历史外置等）的路由前缀。

    deepAgents 把**被压缩掉的对话历史**写到 ``{artifacts_root}/conversation_history/``。
    默认的 ``/`` 会落进工作区 —— 用户在工作区里就能看到自己的对话历史
    （含脱敏可能遗漏的内容）。所以这里指到工作区**外面**的目录，
    跟技能导出目录同理：agent 的文件工具不该看见这些。
    """
    return ARTIFACTS_ROUTE


def _sandbox_backend(context: ExecutionContext, workspace: Path) -> Any:
    """命令执行的后端。

    ``docker`` 模式用我们的 :class:`DockerSandbox`（断网、限资源、限权、只挂载
    工作区）；``local`` 模式用 deepAgents 的 ``LocalShellBackend`` —— 它**没有
    任何隔离**，只适合本地开发。生产必须是 docker。

    docker 模式下容器**按会话复用**（见 ``container_pool.py``）：agent 装的环境、
    设的变量、cd 的目录都跨轮次保留。每次都新起容器的话这些全丢。
    """
    if context.sandbox_type == "local":
        from deepagents.backends.local_shell import LocalShellBackend

        return LocalShellBackend(root_dir=workspace)
    return DockerSandbox(
        workspace,
        session_id=context.thread_id,
        image=context.sandbox_image,
        mount=context.docker_mount,
        timeout=context.sandbox_timeout,
    )


def backend_tools() -> list[Any]:
    """deepAgents 内置工具清单（供文档与测试引用）。

    deepAgents 的文件工具由 ``FilesystemMiddleware`` 注册，不走 ``tools=`` 参数；
    这里列出来是为了让"模型能看到哪些工具"这件事有单一事实来源。
    """
    return [
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    ]


__all__ = ["backend_tools", "build_backend"]
