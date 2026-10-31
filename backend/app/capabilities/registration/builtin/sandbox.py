"""代码与命令执行：一律在沙箱里跑。

两种沙箱模式（`SANDBOX_TYPE`）：

- ``docker``（推荐，生产用）：容器内执行，``--network none`` 断网、限内存/进程数、
  ``--cap-drop ALL``、只挂载当前会话的工作区，宿主文件系统不可见。
- ``local``（仅开发用）：直接用本机 python / sh 子进程执行。**没有任何隔离**，
  agent 生成的代码能读写本机任何文件、能访问网络。绝不要在生产或处理不可信输入时使用。

两种模式都强制：超时中断、不走 shell 拼接（避免注入）、限制工作目录。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext

_MAX_OUTPUT = 20000

# 需要透传给容器进程的 docker 连接信息
_DOCKER_ENV_KEYS = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
)


# ---------- 对外的工厂函数（注册表的入口点） ----------


def make_run_python(context: ExecutionContext) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """在工作区沙箱里执行一段 Python 代码。"""

    async def run_python(code: str) -> dict[str, Any]:
        """在工作区的沙箱里执行一段 Python 代码，返回退出码、标准输出和标准错误。

        代码在当前目录下运行。
        """
        started = time.perf_counter()
        exit_code, stdout, stderr = await exec_python(code, context)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "content": _format(exit_code, stdout, stderr),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    return run_python


def make_run_bash(context: ExecutionContext) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """在工作区沙箱里执行一条 shell 命令。"""

    async def run_bash(command: str) -> dict[str, Any]:
        """在工作区的沙箱里执行一条 shell 命令，返回退出码、标准输出和标准错误。

        命令在当前目录下运行。隔离程度取决于沙箱模式：docker 模式下断网且只能
        看到工作区，local 模式没有隔离，命令能访问本机的一切。
        """
        started = time.perf_counter()
        exit_code, stdout, stderr = await exec_command(command, context)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "content": _format(exit_code, stdout, stderr),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    return run_bash


# ---------- 执行实现 ----------


async def exec_python(code: str, context: ExecutionContext) -> tuple[int, str, str]:
    """在沙箱里执行一段 Python 代码。"""
    script = _write_script(code, context.workspace, suffix=".py")
    try:
        if context.sandbox_type == "docker":
            mount = context.docker_mount
            argv = [
                *_docker_prefix(context),
                context.sandbox_image,
                "python",
                f"{mount}/{script.name}",
            ]
        else:
            argv = [sys.executable, str(script)]
        return await _spawn(argv, context)
    finally:
        script.unlink(missing_ok=True)


async def exec_command(command: str, context: ExecutionContext) -> tuple[int, str, str]:
    """在沙箱里执行一条 shell 命令。

    命令写进脚本文件再执行，而不是 `sh -c "用户输入"` —— 避免引号与元字符
    在拼接层出问题。
    """
    script = _write_script(command, context.workspace, suffix=".sh")
    try:
        if context.sandbox_type == "docker":
            mount = context.docker_mount
            argv = [
                *_docker_prefix(context),
                context.sandbox_image,
                "sh",
                f"{mount}/{script.name}",
            ]
        else:
            argv = ["sh", str(script)]
        return await _spawn(argv, context)
    finally:
        script.unlink(missing_ok=True)


def _docker_prefix(context: ExecutionContext) -> list[str]:
    """docker run 的公共参数：断网、限资源、限权、只挂载工作区。"""
    return [
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
        f"{Path(context.workspace).resolve()}:{context.docker_mount}",
        "-w",
        _docker_workdir(context),
    ]


def _docker_workdir(context: ExecutionContext) -> str:
    """容器里的工作目录：把当前目录换算成挂载点下的相对路径。"""
    mount = context.docker_mount.rstrip("/")
    try:
        relative = Path(context.workdir).resolve().relative_to(Path(context.workspace).resolve())
    except ValueError:  # pragma: no cover - 当前目录总在工作区内
        return mount
    if str(relative) == ".":
        return mount
    return f"{mount}/{relative}"


async def _spawn(argv: list[str], context: ExecutionContext) -> tuple[int, str, str]:
    """起进程、收输出、超时中断。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(context.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(context),
        )
    except FileNotFoundError as exc:
        raise CapabilityError(
            f"找不到可执行文件: {argv[0]}（{exc}）",
            code=ErrorCode.EXECUTION_ERROR,
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=context.sandbox_timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise CapabilityError(
            f"执行超时（>{context.sandbox_timeout}s），已终止",
            code=ErrorCode.TIMEOUT,
            retryable=True,
        ) from exc
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _child_env(context: ExecutionContext) -> dict[str, str]:
    """子进程环境：只保留最小集合，避免把宿主机的 token、代理设置泄进 agent 进程。"""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(context.workspace),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(_docker_env())
    return env


def _docker_env() -> dict[str, str]:
    """把 docker 连接信息透传给子进程。

    子进程的 HOME 被换成了工作区（避免 agent 读到 ~/.aws 之类的凭据），
    于是 docker CLI 找不到 ~/.docker/config.json 里的当前 context，只会去连
    默认的 /var/run/docker.sock —— 用 colima 或 Docker Desktop 的 socket 时
    就会报 "failed to connect to the docker API"。

    所以这里在父进程里把连接信息问出来，直接以 DOCKER_HOST 传下去。
    """
    env = {key: os.environ[key] for key in _DOCKER_ENV_KEYS if os.environ.get(key)}
    if "DOCKER_HOST" not in env:
        endpoint = _docker_endpoint()
        if endpoint:
            env["DOCKER_HOST"] = endpoint
            # 两者同时存在时以 DOCKER_HOST 为准，去掉 context 免得歧义
            env.pop("DOCKER_CONTEXT", None)
    return env


@lru_cache(maxsize=1)
def _docker_endpoint() -> str:
    """当前 docker context 的 endpoint，形如 unix:///path/to/docker.sock。"""
    if shutil.which("docker") is None:
        return ""
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _write_script(code: str, workspace: Path, suffix: str) -> Path:
    """把代码/命令写到工作区内的临时文件并返回路径。"""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        prefix="_agent_",
        dir=workspace,
        delete=False,
        encoding="utf-8",
    )
    with handle:
        handle.write(code)
    return Path(handle.name)


def _format(exit_code: int, stdout: str, stderr: str) -> str:
    """把执行结果渲染成给模型看的文本。"""
    parts = [f"退出码: {exit_code}"]
    if stdout:
        parts.append(f"stdout:\n{_truncate(stdout)}")
    if stderr:
        parts.append(f"stderr:\n{_truncate(stderr)}")
    return "\n".join(parts)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n... (输出过长，已截断，共 {len(text)} 字符)"
