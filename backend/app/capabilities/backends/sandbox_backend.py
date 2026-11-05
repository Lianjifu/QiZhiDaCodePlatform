"""Docker 沙箱后端：把我们的 docker 隔离接成 deepAgents 的 backend。

## 这个后端**只提供 execute**

deepAgents 的 ``BaseSandbox`` 是"只实现 ``execute()``，其余文件操作由它在沙箱里
跑命令实现"的抽象基类。**但那些文件方法在我们这儿是不能用的** ——
它们把虚拟路径**原样**塞进容器命令：

```python
# BaseSandbox.read
result = self.execute(_build_read_cmd(file_path, offset, limit))
# 生成的命令里是：path = base64.b64decode('Lw==').decode('utf-8')   ← "/"
```

我们的工作区挂在容器里的 ``/workspace``，虚拟路径 ``/a.txt`` 在容器里**根本
不存在**（那是容器的根目录）。而 ``upload_files`` / ``download_files`` 我们
实现的是**工作区相对**路径 —— 三方基准不一致。

所以文件操作一律交给宿主侧的 ``FilesystemBackend``（见 ``assembly.py`` 的
route ``"/"``）：快（不用每次起容器），而且路径天然就是虚拟路径。

**本类覆写了全部文件方法**：改单个文件的操作（读 / 写 / 改 / 删）让误用立刻
炸出来，而不是静默返回"文件不存在"；**查找类**（列目录 / glob / grep）返回
**空结果** —— 容器里的文件树不是平台的文件树（工作区挂在 ``/workspace``，
平台的读写用宿主后端），把容器的系统文件当成工作区内容返回才是错的。

隔离参数原样保留（``--network none`` / ``--cap-drop ALL`` / 限内存限进程），
只挂载会话工作区 —— 这是 deepAgents 自带的 ``LocalShellBackend`` 给不了的
（它是 ``subprocess.run(shell=True)``，没有任何隔离）。

## 容器按会话复用

容器**常驻**，按会话标识命名。复用带来的收益：

| 保留 | 不保留 |
| --- | --- |
| 装好的包（pip / npm install…） | **环境变量**（``export FOO=1``） |
| 容器里生成的文件 | **当前目录**（``cd``） |
| 跑起来的后台进程 | |

后两项不保留是 ``docker exec`` 的固有语义 —— **每次 exec 都是新进程**。
要保留它们得改成"持久 shell + 管道"的实现（复杂，且 shell 状态跨请求容易
失控），当前不做。

所以 agent 装依赖、跑构建这类操作是有效的；但"设个环境变量，下一条命令要用"
这种**同一条命令内**的用法才成立 —— agent 得把 export 和使用写在同一条命令里。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from app.capabilities.backends.container_pool import (
    KEEPALIVE_COMMAND,
    container_name,
    get_pool,
    inspect_container,
    remove_container,
)

try:  # pragma: no cover - 依赖缺失时给出明确提示
    from deepagents.backends.sandbox import BaseSandbox
except ImportError as exc:  # pragma: no cover
    raise ImportError("缺少 deepagents 依赖，请先安装：pip install 'deepagents==0.7.32'") from exc

logger = logging.getLogger(__name__)

# (工作区, 镜像) → 挂载预检已通过
#
# **进程级共享** —— 同一份工作区 + 同一个镜像的结果不会变。
_MOUNT_OK: set[tuple[str, str]] = set()

# docker daemon 自己报错的固定开头
#
# **只认这一行开头** —— 命令自己往 stderr 打的话不算。认错了会把命令
# 跑两遍，副作用也就跑两遍。
_DOCKER_DAEMON_ERROR = "Error response from daemon"

# 容器没了时 daemon 会说的那几句话
_CONTAINER_GONE_MARKERS = ("no such container", "is not running")

# 单次执行输出的最大字符数：命令可能打印巨量内容，全塞进模型上下文会挤爆它
_MAX_OUTPUT = 20000

# 挂载预检用的标记文件：写进工作区，进容器后确认它真的看得见
_MOUNT_CHECK_FILE = ".qzd_mount_check"

# 误用文件方法时的提示
_FILE_METHODS_UNSUPPORTED = (
    "DockerSandbox 只提供 execute，文件操作不走这里。"
    "原因：基类把虚拟路径原样塞进容器命令，而我们的工作区挂在 {mount} —— "
    "虚拟路径在容器里不存在。文件操作由宿主侧的 FilesystemBackend 负责。"
)

# 挂载没生效时的提示
_MOUNT_FAILED = (
    "工作区没有挂载进容器: {workspace}\n"
    "docker 运行时看不到这个目录（macOS 上 Docker Desktop / Colima "
    "只能挂载被共享的路径，Colima 默认共享 $HOME）。\n"
    "解决办法：把 WORKSPACE_ROOT 放到共享目录下，"
    "或把该目录加入 docker 的共享列表。"
)

# 标记文件在容器里出现得慢一点时，重试几次
_MOUNT_VISIBLE_ATTEMPTS = 6
_MOUNT_VISIBLE_INTERVAL = 0.05


def _container_gone(completed: subprocess.CompletedProcess[str]) -> bool:
    """这次 exec 是不是因为容器没了才失败的。

    **只看 docker daemon 自己的报错**（``Error response from daemon: …``）——
    命令自己往 stderr 打 "no such container"（比如在脚本里 grep 这句话）
    不算，那不是容器没了；认错了会**把命令跑两遍**（副作用也就跑两遍）。

    退出码不靠谱：``docker exec`` 对"容器不存在"给的是 1，不是 125。
    """
    detail = (completed.stderr or "").strip()
    if not detail.startswith(_DOCKER_DAEMON_ERROR):
        return False
    lowered = detail.lower()
    return any(marker in lowered for marker in _CONTAINER_GONE_MARKERS)


class DockerSandbox(BaseSandbox):
    """在 docker 容器里执行命令的后端。

    **只提供 execute** —— 改单个文件的操作（读 / 写 / 改 / 删）由宿主侧的
    ``FilesystemBackend`` 负责（原因见模块说明），误用会立刻炸出来；
    查找类（列目录 / glob / grep）返回**空结果** —— 容器里的文件树不是
    平台的文件树。

    容器**按会话复用**（见 ``container_pool.py``）：环境变量、cd、装好的依赖
    都跨轮次保留。
    """

    def __init__(
        self,
        workspace: Path,
        *,
        session_id: str = "",
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
            session_id: 会话标识。**容器按它复用** —— 不同会话各自一个容器，
                互不干扰。留空则按工作区名兜底。
            image: 容器镜像。
            mount: 工作区在容器里的挂载点。
            timeout: 单次执行的默认超时（秒）。
            memory: 容器内存上限。
            cpus: 容器 CPU 上限。
            pids_limit: 容器进程数上限。
        """
        self.workspace = Path(workspace).resolve()
        # 会话标识决定容器名：同一个会话一直用同一个容器
        self.session_id = session_id or self.workspace.name
        self.image = image
        self.mount = mount.rstrip("/")
        self.timeout = timeout
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        # 挂载预检的结果 —— **进程级共享**，见 ``_MOUNT_OK``。
        self._mount_ok: bool | None = None

    @property
    def id(self) -> str:
        """沙箱标识。"""
        return f"docker:{self.workspace.name}"

    # ---------- 文件方法：不支持 ----------

    def _unsupported(self) -> NotImplementedError:
        """构造"文件方法不可用"的异常。

        基类的文件方法把**虚拟路径原样**塞进容器命令（``/a.txt``），而我们的
        工作区挂在 ``/workspace`` —— 那个路径在容器里不存在，基类会返回
        "文件不存在"这种**看起来像正常结果**的错误。宁可响亮地炸掉。
        """
        return NotImplementedError(_FILE_METHODS_UNSUPPORTED.format(mount=self.mount))

    # ---------- 查找类：空结果 ----------

    # 容器里的文件树**不是**平台的文件树：工作区挂在 ``/workspace``，
    # 平台的读读写用宿主后端（route "/"）。基类会拿虚拟路径去容器里跑
    # ``ls`` / ``grep`` / ``find`` —— 搜出来的是容器的系统文件（/usr/share…），
    # 不是工作区的内容。
    #
    # 所以这里返回空结果：``CompositeBackend`` 会把默认后端的结果与各路由的
    # 结果**合并**，空结果不影响宿主后端给出的真实命中；而报错会让整个
    # glob / grep 一起失败（框架不捕获后端异常）。

    def ls(self, path: str) -> LsResult:
        """列目录 —— 容器里没有平台的文件树，返回空。"""
        return LsResult(entries=[])

    async def als(self, path: str) -> LsResult:
        """异步列目录 —— 容器里没有平台的文件树，返回空。"""
        return LsResult(entries=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """按名字模式找文件 —— 容器里没有平台的文件树，返回空。"""
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步按名字模式找文件 —— 容器里没有平台的文件树，返回空。"""
        return GlobResult(matches=[])

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """搜文件内容 —— 容器里没有平台的文件树，返回空。

        签名**必须**与框架一致：``CompositeBackend`` 按三个位置参数调用
        ``grep(pattern, path, glob)`` 并可能带 ``max_count``。签名对不上
        （比如只收两个位置参数）会在 docker 模式下直接抛 ``TypeError``。
        """
        return GrepResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """异步搜文件内容 —— 容器里没有平台的文件树，返回空。"""
        return GrepResult(matches=[])

    # ---------- 改单个文件：响亮地炸 ----------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise self._unsupported()

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise self._unsupported()

    def write(self, file_path: str, content: str) -> WriteResult:
        raise self._unsupported()

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        raise self._unsupported()

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        raise self._unsupported()

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        raise self._unsupported()

    def delete(self, file_path: str) -> DeleteResult:
        raise self._unsupported()

    async def adelete(self, file_path: str) -> DeleteResult:
        raise self._unsupported()

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
            limit = timeout or self.timeout
            for attempt in (1, 2):
                name = self._ensure_container()
                get_pool().touch(self.session_id)
                argv = [
                    "docker",
                    "exec",
                    # 每次都回到工作区根 —— 容器是常驻的，上一轮的 cd 会留在里面
                    "-w",
                    self.mount,
                    name,
                    "sh",
                    f"{self.mount}/{script.name}",
                ]
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
                # **容器在探活之后被删了**（外部清理、机器重启）——
                # 探活结果有几秒的缓存期，这段时间里容器没了是可能的。
                # 忘掉缓存重建一次，别让 agent 拿到一句莫名其妙的
                # "No such container" 就以为命令失败了。
                if attempt == 1 and _container_gone(completed):
                    logger.info("沙箱容器 %s 没了，重建后重试", name)
                    get_pool().forget_probe(name)
                    remove_container(name)
                    continue
                return ExecuteResponse(
                    output=self._truncate(output),
                    exit_code=completed.returncode,
                    truncated=len(output) > _MAX_OUTPUT,
                )
            # 两轮都走到这儿 —— 理论上不会（第二轮一定 return）
            return ExecuteResponse(
                output=self._truncate(output),
                exit_code=completed.returncode,
                truncated=len(output) > _MAX_OUTPUT,
            )
        finally:
            script.unlink(missing_ok=True)

    def _ensure_container(self) -> str:
        """确保这个会话有一个活着的容器，返回容器名。

        容器是**按会话复用**的 —— 环境变量、cd、装好的依赖都留着。

        探活发现容器没了（被外部清理、机器重启）就重建。

        **探活结果先看缓存** —— 每次执行命令前都要探一次活，而探活是
        一次 docker 子进程（二十多毫秒）。一轮对话里跑几条命令就是几次
        进程启动，全是白花的钱。缓存几秒钟就够：容器被外部删掉的话，
        紧接着的 ``docker exec`` 会失败，那时报错重试也来得及。

        Returns:
            容器名。

        Raises:
            RuntimeError: 容器建不起来。
        """
        name = container_name(self.session_id)
        pool = get_pool()
        cached = pool.probe_result(name)
        if cached is not None:
            running, mounted = cached
            if running and mounted is not None and Path(mounted).resolve() == self.workspace:
                return name
        else:
            running, mounted = inspect_container(name, self.mount)
            pool.remember_probe(name, running, mounted)
            if running and mounted is not None and Path(mounted).resolve() == self.workspace:
                return name
        if running:
            # 复用的容器可能挂载着**别的工作区**（平台重启前建的、或者
            # 工作区被重新分配过）。挂载不对的话，agent 写的文件会落到
            # 别人的目录里 —— 静默但危险。校验不过就重建。
            logger.info(
                "沙箱容器 %s 挂载的工作区不对（%s ≠ %s），重建",
                name,
                mounted,
                self.workspace,
            )
            remove_container(name)
            pool.forget_probe(name)
        # 常驻容器占内存 —— 太多的话先回收最久没用的
        get_pool().enforce_capacity()
        argv = [
            "docker",
            "run",
            "-d",  # 后台常驻，不随命令结束而退出
            "--name",
            name,
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
            *KEEPALIVE_COMMAND,
        ]
        try:
            completed = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"创建沙箱容器失败: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            # 创建失败可能留下一个占着名字的容器（名字冲突、启动失败等）。
            # 不清掉的话下次再建还是失败 —— 而且那个容器是死的，白占资源。
            remove_container(name)
            raise RuntimeError(f"创建沙箱容器失败: {detail}")
        logger.info("会话 %s 的沙箱容器已创建: %s", self.session_id, name)
        # **记下"它活着，而且挂的是这个工作区"** —— 刚才那次探活问的是
        # "还没建的时候"，记下来的"没有"马上就过期了。下一条命令要是照着
        # 那条过期记录走，就会去建第二个同名容器（docker 直接报重名）。
        pool.remember_probe(name, True, str(self.workspace))
        return name

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """异步执行（docker 调用丢线程池，不占着事件循环）。

        **同一会话串行执行** —— 容器是常驻的，共享 cwd 和环境变量，并行会
        打架（一个命令 ``cd`` 了，另一个还在原地）。不同会话各有容器，可以并行。
        """
        lock = get_pool().lock_for(self.session_id)
        async with lock:
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

        所以这里放一个标记文件，进容器确认它看得见；看不见就明确报错，
        把"没共享目录"这件事直接说出来。

        **在会话容器里验，不再另起一个一次性容器** —— 以前这里要
        ``docker run --rm`` 起一个临时容器（一百多毫秒），验完就删，
        紧接着还要再起一个常驻容器 —— 两次容器创建，全是白花的钱。
        现在直接在常驻容器里放标记文件看一眼。

        Raises:
            RuntimeError: 工作区没有挂进容器。
        """
        # **预检结果按 (工作区, 镜像) 共享** —— 同一个工作区 + 同一个镜像，
        # 挂进去的结果不会变，没必要每次命令都再验一遍。
        cache_key = (str(self.workspace), self.image)
        if self._mount_ok or cache_key in _MOUNT_OK:
            self._mount_ok = True
            return

        marker = self.workspace / _MOUNT_CHECK_FILE
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"工作区不可写: {self.workspace}（{exc}）") from exc

        try:
            name = self._ensure_container()
            if not self._marker_visible(name, marker):
                # 挂载没生效 —— 容器留着也没用（而且会占着名字），
                # 删掉免得下次建容器撞名。
                remove_container(name)
                get_pool().forget_probe(name)
                raise RuntimeError(_MOUNT_FAILED.format(workspace=self.workspace))
            _MOUNT_OK.add(cache_key)
            self._mount_ok = True
        finally:
            marker.unlink(missing_ok=True)

    def _marker_visible(self, name: str, marker: Path) -> bool:
        """标记文件在容器里看得见吗。

        **要重试几次** —— 容器是复用的时候，标记文件是刚写到宿主上的，
        macOS 的 Docker Desktop 要靠文件共享把它同步进容器，不是立刻的。
        """
        target = f"{self.mount}/{marker.name}"
        for attempt in range(_MOUNT_VISIBLE_ATTEMPTS):
            completed = subprocess.run(  # noqa: S603
                ["docker", "exec", name, "test", "-f", target],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if completed.returncode == 0:
                return True
            if attempt + 1 < _MOUNT_VISIBLE_ATTEMPTS:
                time.sleep(_MOUNT_VISIBLE_INTERVAL)
        return False

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
