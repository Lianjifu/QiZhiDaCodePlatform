"""Docker 沙箱后端：把我们的 docker 隔离接成 deepAgents 的 backend。

deepAgents 的 ``BaseSandbox`` 是"只实现 ``execute()``，其余文件操作由它在沙箱里
跑命令实现"的抽象基类。我们的 docker 沙箱正好是这个形状。

隔离参数原样保留（``--network none`` / ``--cap-drop ALL`` / 限内存限进程），
只挂载会话工作区 —— 这是 deepAgents 自带的 ``LocalShellBackend`` 给不了的
（它是 ``subprocess.run(shell=True)``，没有任何隔离）。

命令**写进脚本文件再执行**，不做 ``sh -c "<用户输入>"`` 拼接：避免引号与元字符
在拼接层出问题。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)

try:  # pragma: no cover - 依赖缺失时给出明确提示
    from deepagents.backends.sandbox import BaseSandbox
except ImportError as exc:  # pragma: no cover
    raise ImportError("缺少 deepagents 依赖，请先安装：pip install 'deepagents==0.7.32'") from exc

# 单次执行输出的最大字符数：命令可能打印巨量内容，全塞进模型上下文会挤爆它
_MAX_OUTPUT = 20000

# 挂载预检用的标记文件：写进工作区，进容器后确认它真的看得见
_MOUNT_CHECK_FILE = ".qzd_mount_check"


class DockerSandbox(BaseSandbox):
    """在 docker 容器里执行命令的后端。

    只实现 deepAgents 要求的四个抽象成员（``execute`` / ``id`` /
    ``upload_files`` / ``download_files``），文件操作交给 ``BaseSandbox``
    在容器内跑命令实现。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        image: str = "python:3.13-slim",
        mount: str = "/workspace",
        timeout: int = 60,
        memory: str = "512m",
        cpus: str = "1",
        pids_limit: int = 256,
    ) -> None:
        """构造沙箱。

        Args:
            workspace: 会话工作区（挂进容器的唯一目录）。
            image: 容器镜像。
            mount: 工作区在容器里的挂载点。
            timeout: 单次执行的默认超时（秒）。
            memory: 容器内存上限。
            cpus: 容器 CPU 上限。
            pids_limit: 容器进程数上限。
        """
        self.workspace = Path(workspace).resolve()
        self.image = image
        self.mount = mount.rstrip("/")
        self.timeout = timeout
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self._mount_ok: bool | None = None

    @property
    def id(self) -> str:
        """沙箱标识。"""
        return f"docker:{self.workspace.name}"

    # ---------- 执行 ----------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在容器里执行一条 shell 命令。

        Args:
            command: 命令原文。
            timeout: 本次超时（秒），不传用构造时的默认值。

        Returns:
            合并了 stdout/stderr 的输出与退出码。

        Raises:
            RuntimeError: 找不到 docker 可执行文件，或工作区没有挂载进容器。
        """
        if shutil.which("docker") is None:
            raise RuntimeError("找不到 docker 可执行文件，沙箱不可用")

        self._ensure_mounted()

        script = self._write_script(command)
        try:
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",  # 断网，阻断数据外泄
                "--memory",
                self.memory,
                "--cpus",
                self.cpus,
                "--pids-limit",
                str(self.pids_limit),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{self.workspace}:{self.mount}",
                "-w",
                self.mount,
                self.image,
                "sh",
                f"{self.mount}/{script.name}",
            ]
            limit = timeout or self.timeout
            try:
                completed = subprocess.run(  # noqa: S603
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=limit,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return ExecuteResponse(
                    output=f"执行超时（>{limit}s），已终止",
                    exit_code=None,
                    truncated=False,
                )
            output = (completed.stdout or "") + (completed.stderr or "")
            return ExecuteResponse(
                output=self._truncate(output),
                exit_code=completed.returncode,
                truncated=len(output) > _MAX_OUTPUT,
            )
        finally:
            script.unlink(missing_ok=True)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """异步执行（docker 调用丢线程池，不占着事件循环）。"""
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    # ---------- 文件传输 ----------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """把文件写进挂载的工作区。

        Args:
            files: ``(路径, 内容)`` 列表，路径是工作区内的相对路径。

        Returns:
            每个文件的写入结果（失败时带 ``error``）。
        """
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                target = self._safe_path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                results.append(FileUploadResponse(path=path))
            except (OSError, ValueError) as exc:
                results.append(FileUploadResponse(path=path, error=str(exc)))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从挂载的工作区读文件。

        Args:
            paths: 工作区内的相对路径列表。

        Returns:
            每个文件的读取结果（失败时 ``content`` 为 ``None``、带 ``error``）。
        """
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = self._safe_path(path)
                results.append(FileDownloadResponse(path=path, content=target.read_bytes()))
            except (OSError, ValueError) as exc:
                results.append(FileDownloadResponse(path=path, error=str(exc)))
        return results

    # ---------- 内部 ----------

    def _ensure_mounted(self) -> None:
        """确认工作区真的挂进了容器（每个沙箱实例只查一次）。

        macOS 上 Docker Desktop / Colima 运行在 Linux 虚拟机里，**只有被显式
        共享的目录**才能 bind mount 进去（Colima 默认共享 ``$HOME``）。
        路径没共享时，docker 不会报错，而是挂一个**空目录**进去 ——
        容器里的命令就在一个空工作区里跑，报错信息还特别误导
        （"cannot open /workspace/xxx.sh: No such file"）。

        所以这里先放一个标记文件，进容器确认它看得见；看不见就明确报错，
        把"没共享目录"这件事直接说出来。

        Raises:
            RuntimeError: 工作区没有挂进容器。
        """
        if self._mount_ok:
            return

        marker = self.workspace / _MOUNT_CHECK_FILE
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"工作区不可写: {self.workspace}（{exc}）") from exc

        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{self.workspace}:{self.mount}",
            "-w",
            self.mount,
            self.image,
            "sh",
            "-c",
            f"test -f {self.mount}/{_MOUNT_CHECK_FILE}",
        ]
        try:
            completed = subprocess.run(  # noqa: S603
                argv, capture_output=True, text=True, timeout=60, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"挂载预检失败: {exc}") from exc
        finally:
            marker.unlink(missing_ok=True)

        if completed.returncode != 0:
            raise RuntimeError(
                f"工作区没有挂载进容器: {self.workspace}\n"
                f"docker 运行时看不到这个目录（macOS 上 Docker Desktop / Colima "
                f"只能挂载被共享的路径，Colima 默认共享 $HOME）。\n"
                f"解决办法：把 WORKSPACE_ROOT 放到共享目录下，"
                f"或把该目录加入 docker 的共享列表。"
            )
        self._mount_ok = True

    def _safe_path(self, path: str) -> Path:
        """把相对路径解析到工作区内。

        Raises:
            ValueError: 路径越出工作区。
        """
        candidate = (self.workspace / path.lstrip("/")).resolve()
        root = self.workspace
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"路径超出工作区范围: {path}")
        return candidate

    def _write_script(self, command: str) -> Path:
        """把命令写进工作区内的临时脚本文件并返回路径。

        写在工作区里（不是系统临时目录）：容器只挂载了工作区，
        写在别处的脚本容器里根本看不见。
        """
        self.workspace.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".sh",
            prefix="_agent_",
            dir=self.workspace,
            delete=False,  # 执行完由调用方负责删（跨 await，自动删不适用）
            encoding="utf-8",
        )
        with handle:
            handle.write(command)
        return Path(handle.name)

    @staticmethod
    def _truncate(text: str) -> str:
        """截断过长的输出，并说明原始长度。"""
        if len(text) <= _MAX_OUTPUT:
            return text
        return text[:_MAX_OUTPUT] + f"\n... (输出过长，已截断，共 {len(text)} 字符)"


__all__ = ["DockerSandbox"]
