"""执行 Python 代码：进沙箱跑。

**命令执行已经由 deepAgents 接管**（工具名 ``execute``，见
``app/capabilities/backends/sandbox_backend.py``），这里只保留 ``run_python``
—— deepAgents 没有等价物。

实现直接复用同一个 docker 沙箱后端，所以两条路径的隔离参数完全一致：
断网、限内存限进程、限权、只挂载会话工作区。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext

logger = logging.getLogger(__name__)


def make_run_python(context: ExecutionContext) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """构造"执行 Python 代码"能力。

    和文件类能力一样是**工厂函数**：把本次执行的上下文固定进闭包，
    保证同一份声明在不同会话里用各自的上下文执行。
    """

    async def run_python(code: str) -> dict[str, Any]:
        """在工作区的沙箱里执行一段 Python 代码，返回退出码与输出。

        代码写进工作区里的临时脚本再执行，不做字符串拼接 ——
        拼接是注入的经典入口。

        Args:
            code: 要执行的 Python 源码。

        Returns:
            ``content`` 是渲染好的文本（给模型看），其余是结构化字段
            （output / exit_code / duration_ms）。

        Raises:
            CapabilityError: 沙箱不可用，或执行超时（``TIMEOUT``）。
        """
        from app.capabilities.backends.sandbox_backend import DockerSandbox

        started = time.perf_counter()
        script = _write_script(code, context.workspace)
        try:
            if context.sandbox_type == "local":
                # local 模式没有隔离，只适合本地开发
                exit_code, output = await _run_local(script, context)
            else:
                sandbox = DockerSandbox(
                    context.workspace,
                    # **跟 execute 用同一个容器** —— 容器名按会话标识算，
                    # 不给就退化成"按工作区目录名算"，于是同一个会话会起
                    # 两个容器：装好的依赖看不到，还白占一份内存。
                    session_id=context.thread_id,
                    image=context.sandbox_image,
                    mount=context.docker_mount,
                    timeout=context.sandbox_timeout,
                )
                try:
                    response = await sandbox.aexecute(
                        f"python {context.docker_mount.rstrip('/')}/{script.name}",
                        timeout=context.sandbox_timeout,
                    )
                except RuntimeError as exc:
                    raise CapabilityError(str(exc), code=ErrorCode.EXECUTION_ERROR) from exc
                exit_code = response.exit_code if response.exit_code is not None else -1
                output = response.output or ""
        finally:
            # **临时脚本必须删** —— 它落在工作区里（容器只挂载工作区，
            # 写在别处容器看不见），不删就会一直堆着，而且会被
            # ``git_commit`` 一起提交进用户的仓库。
            _remove_script(script)

        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "content": _format(exit_code, output),
            "output": output,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    return run_python


async def _run_local(script: Path, context: ExecutionContext) -> tuple[int, str]:
    """在宿主机上跑脚本（**没有隔离**，只用于本地开发）。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        cwd=str(context.workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=context.sandbox_timeout)
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


def _write_script(code: str, workspace: Path) -> Path:
    """把代码写到工作区内的临时脚本文件并返回路径。

    写在工作区里（不是系统临时目录）：容器只挂载了工作区，
    写在别处的脚本容器里根本看不见。
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="_agent_",
        dir=workspace,
        delete=False,  # 执行完由调用方负责删（跨 await，自动删不适用）
        encoding="utf-8",
    )
    with handle:
        handle.write(code)
    return Path(handle.name)


def _remove_script(script: Path) -> None:
    """删掉临时脚本。

    **删不掉也不该让整轮失败** —— 脚本执行完了，剩下的只是一个
    没人要的垃圾文件。为它抛异常会让模型以为执行失败了，
    于是重跑一遍 —— 越跑垃圾越多。
    """
    try:
        script.unlink(missing_ok=True)
    except OSError:
        logger.warning("临时脚本删不掉：%s", script)


def _format(exit_code: int, output: str) -> str:
    """把执行结果渲染成给模型看的文本。

    退出码永远出现在最前面 —— 模型第一眼就能看到"成功还是失败"。
    """
    parts = [f"退出码: {exit_code}"]
    if output:
        parts.append(output)
    return "\n".join(parts)


__all__ = ["make_run_python"]
