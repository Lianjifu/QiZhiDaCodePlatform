"""代码执行工具：在沙箱里运行 agent 生成的代码。

两种模式（`SANDBOX_TYPE`）：

- ``docker``（推荐，生产用）：容器内执行，``--network none`` 断网、限内存/进程数、
  只挂载当前会话的工作区，宿主文件系统不可见。
- ``local``（仅开发用）：直接用本机 python 子进程执行。**没有任何隔离**，
  agent 生成的代码能读写本机任何文件、能访问网络。绝不要在生产或处理不可信输入时使用。

两种模式都强制：超时中断、不走 shell（避免 shell 注入）、限制工作目录。
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.core.config import settings

_MAX_OUTPUT = 20000

# Docker Desktop 在 macOS 上默认只把 /Users 共享给虚拟机，工作区放在别处时
# 容器会挂到一个空目录，表现为"脚本文件不存在"。
_MOUNT_HINT = (
    "提示：容器看不到工作区 {workspace}。\n"
    "macOS 的 Docker Desktop 默认只共享 /Users，请把 WORKSPACE_ROOT 设到 /Users 下，"
    "或在 Docker Desktop → Settings → Resources → File Sharing 里共享该目录。"
)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... (输出过长，已截断，共 {len(text)} 字符)"


async def _run_local(code: str, workspace: Path) -> tuple[int, str, str]:
    """本机子进程执行（无隔离，仅供开发）。"""
    script_path = _write_script(code, workspace)
    try:
        # 只保留最小环境变量，避免把宿主机的 token、代理设置等泄进 agent 进程。
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(workspace),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.sandbox_timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"执行超时（>{settings.sandbox_timeout}s），已终止"
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    finally:
        script_path.unlink(missing_ok=True)


async def _run_docker(code: str, workspace: Path) -> tuple[int, str, str]:
    """容器内执行（隔离，推荐）。"""
    script_path = _write_script(code, workspace)
    mount = settings.agent_docker_mount
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",  # 断网，阻断数据外泄
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-v",
        f"{workspace.resolve()}:{mount}",
        "-w",
        mount,
        settings.sandbox_image,
        "python",
        f"{mount}/{script_path.name}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.sandbox_timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", f"执行超时（>{settings.sandbox_timeout}s），已终止"
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        # 挂载没生效时容器里看不到脚本，只会报 "can't open file"。
        # 这是 macOS Docker Desktop 最常见的坑（默认只共享 /Users），给一句人话提示。
        if "No such file or directory" in stderr_text and script_path.name in stderr_text:
            return (
                proc.returncode or 0,
                stdout_text,
                f"{stderr_text}\n{_MOUNT_HINT.format(workspace=workspace.resolve())}",
            )
        return (proc.returncode or 0, stdout_text, stderr_text)
    except FileNotFoundError:
        return -1, "", "未找到 docker 命令，无法使用 docker 沙箱"
    finally:
        script_path.unlink(missing_ok=True)


def _write_script(code: str, workspace: Path) -> Path:
    """把代码写到工作区内的临时文件并返回路径。"""
    workspace.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        prefix="_agent_",
        dir=workspace,
        delete=False,
        encoding="utf-8",
    )
    with handle:
        handle.write(code)
    return Path(handle.name)


def build_code_tools(workspace: Path) -> list[BaseTool]:
    """生成绑定到指定工作区的代码执行工具。"""

    @tool
    async def run_python(code: str) -> str:
        """在工作区的沙箱里执行一段 Python 代码，返回退出码、标准输出和标准错误。

        代码在会话工作区根目录下运行，可以读写工作区里的文件。
        """
        exit_code, stdout, stderr = await _execute(code, workspace)
        parts = [f"退出码: {exit_code}"]
        if stdout:
            parts.append(f"stdout:\n{_truncate(stdout)}")
        if stderr:
            parts.append(f"stderr:\n{_truncate(stderr)}")
        return "\n".join(parts)

    return [run_python]


async def _execute(code: str, workspace: Path) -> tuple[int, str, str]:
    if settings.sandbox_type == "docker":
        return await _run_docker(code, workspace)
    return await _run_local(code, workspace)
