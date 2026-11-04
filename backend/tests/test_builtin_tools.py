"""内置能力与后端测试。

**文件类能力已经不在我们这里了** —— 它们由 deepAgents 的文件系统中间件提供，
路径安全、读写语义都归它管（``FilesystemBackend`` 的虚拟模式）。所以这个文件
现在测三块：

1. **路径安全**：deepAgents 的 ``FilesystemBackend`` 会不会放越界路径；
2. **我们自己的内置能力**：``run_python`` / ``web_fetch`` / ``web_search``；
3. **沙箱**：docker 模式的隔离（断网、看不见工作区以外的文件）。
"""

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from deepagents.backends.filesystem import FilesystemBackend

from app.capabilities.backends.sandbox_backend import DockerSandbox
from app.capabilities.errors import CapabilityError
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.sandbox import make_run_python
from app.capabilities.registration.builtin.web import _html_to_text, make_web_fetch, make_web_search


@pytest.fixture
def ctx(tmp_path):
    """执行上下文：本地沙箱模式，工作区是临时目录。"""
    context = ExecutionContext.build(workspace=tmp_path)
    context.sandbox_type = "local"
    return context


@pytest.fixture
async def docker_ctx():
    """docker 沙箱上下文。

    工作区放在 ``$HOME`` 下而不是 pytest 的 tmp 目录：容器只能看到宿主
    共享出去的路径（Docker Desktop 共享 /Users，colima 共享 $HOME），
    pytest 的 tmp_path 在 /var/folders 下，容器里是空的。
    """
    if not _docker_daemon_ready():
        pytest.skip("docker daemon 不可用")
    workspace = Path.home() / ".qzd-docker-tests" / uuid4().hex
    workspace.mkdir(parents=True, exist_ok=True)
    context = ExecutionContext.build(workspace=workspace)
    context.sandbox_type = "docker"
    context.sandbox_image = "python:3.12-slim"

    sandbox = DockerSandbox(workspace, image=context.sandbox_image)
    try:
        probe = sandbox.execute("echo probe")
    except RuntimeError:
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("docker 容器看不到工作区（当前虚拟机只共享部分宿主路径）")
    if "probe" not in (probe.output or ""):
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("docker 容器看不到工作区（当前虚拟机只共享部分宿主路径）")
    try:
        yield context
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _docker_daemon_ready() -> bool:
    """docker CLI 存在 **且** daemon 可用。

    只看 CLI 是不够的：装了 Docker Desktop 但没启动时 CLI 在、socket 不在，
    容器测试会以 "failed to connect to the docker API" 失败。
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# ---------- 路径安全（deepAgents 的虚拟文件系统） ----------


def test_relative_path_resolves_inside_workspace(tmp_path):
    backend = FilesystemBackend(root_dir=tmp_path)
    assert backend.write("/src/main.py", "x").error is None
    assert (tmp_path.resolve() / "src" / "main.py").exists()


def test_parent_traversal_is_rejected(tmp_path):
    """``..`` 一律拒绝 —— 虚拟模式不解析它，直接报错。"""
    backend = FilesystemBackend(root_dir=tmp_path)
    with pytest.raises(ValueError, match="Path traversal"):
        backend.write("/../secret.txt", "x")
    assert not (tmp_path.parent / "secret.txt").exists()


def test_absolute_path_is_mapped_inside_the_root(tmp_path):
    """绝对路径在虚拟模式下是**工作区里的**路径，不是宿主的根。

    所以 ``/etc/passwd`` 指向的是 ``<工作区>/etc/passwd`` —— 宿主上真的
    ``/etc/passwd`` 读不到。
    """
    backend = FilesystemBackend(root_dir=tmp_path)
    result = backend.read("/etc/passwd")
    assert result.error is not None
    assert result.file_data is None


def test_symlink_escape_is_rejected(tmp_path):
    """符号链接指向工作区外也要挡住。"""
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s3cret", encoding="utf-8")
    (workspace / "link").symlink_to(outside)

    backend = FilesystemBackend(root_dir=workspace)
    with pytest.raises(ValueError):
        backend.read("/link/secret.txt")


# ---------- 内置能力：执行 Python ----------


@pytest.mark.asyncio
async def test_run_python_executes_code(ctx):
    out = await make_run_python(ctx)("print('hi from sandbox')")
    assert out["exit_code"] == 0
    assert "hi from sandbox" in out["output"]
    assert "退出码: 0" in out["content"]


@pytest.mark.asyncio
async def test_run_python_reports_nonzero_exit(ctx):
    out = await make_run_python(ctx)("raise SystemExit(3)")
    assert out["exit_code"] == 3


@pytest.mark.asyncio
async def test_run_python_deletes_its_temp_script(ctx):
    """临时脚本跑完必须删掉。

    它落在**工作区里**（容器只挂载工作区，写在别处容器看不见）——
    不删就会一直堆着，而且会被 ``git_commit`` 一起提交进用户的仓库。
    """
    await make_run_python(ctx)("print('hi')")
    leftovers = [p.name for p in ctx.workspace.glob("_agent_*.py")]
    assert not leftovers, f"临时脚本没删：{leftovers}"


@pytest.mark.asyncio
async def test_run_python_deletes_temp_script_even_on_failure(ctx):
    """脚本**跑炸了**也要删 —— 失败路径最容易漏。"""
    await make_run_python(ctx)("raise RuntimeError('boom')")
    leftovers = [p.name for p in ctx.workspace.glob("_agent_*.py")]
    assert not leftovers, f"临时脚本没删：{leftovers}"


@pytest.mark.asyncio
async def test_run_python_can_read_workspace_files(ctx):
    (ctx.workspace / "data.txt").write_text("42", encoding="utf-8")
    out = await make_run_python(ctx)("print(open('data.txt').read().strip())")
    assert "42" in out["output"]


@pytest.mark.asyncio
async def test_local_sandbox_is_not_isolated(ctx, tmp_path):
    """local 模式没有隔离 —— 这是它只适合开发用的原因。

    隔离是 docker 模式的属性；local 模式下 agent 的代码能访问宿主机上
    工作区以外的任何文件。这里把这条事实固定下来，避免误以为 local 也是沙箱。
    """
    secret = tmp_path.parent / "outside_secret.txt"
    secret.write_text("s3cret", encoding="utf-8")
    out = await make_run_python(ctx)("print(open('../outside_secret.txt').read())")
    assert "s3cret" in out["output"]


# ---------- 内置能力：网络 ----------


def test_html_to_text_strips_tags_and_scripts():
    raw = "<html><head><style>a{}</style><script>x()</script></head><body><p>你好</p></body></html>"
    text = _html_to_text(raw)
    assert "你好" in text
    assert "<" not in text
    assert "x()" not in text


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_url(ctx):
    with pytest.raises(CapabilityError) as exc:
        await make_web_fetch(ctx)("file:///etc/passwd")
    assert "只支持 http/https" in exc.value.message


@pytest.mark.asyncio
async def test_web_search_without_key_gives_actionable_hint(ctx, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.web_search_provider", "")
    monkeypatch.setattr("app.core.config.settings.web_search_api_key", "")
    with pytest.raises(CapabilityError) as exc:
        await make_web_search(ctx)("langchain")
    assert "WEB_SEARCH_PROVIDER" in exc.value.message


# ---------- docker 沙箱 ----------


@pytest.mark.asyncio
async def test_docker_sandbox_runs_python(docker_ctx):
    out = await make_run_python(docker_ctx)("print('docker ok')")
    assert out["exit_code"] == 0
    assert "docker ok" in out["output"]


@pytest.mark.asyncio
async def test_docker_sandbox_has_no_network(docker_ctx):
    """docker 模式下必须断网（--network none），容器内任何出网请求都要失败。"""
    out = await make_run_python(docker_ctx)(
        "import socket;socket.create_connection(('1.1.1.1',53),timeout=3)"
    )
    assert out["exit_code"] != 0


@pytest.mark.asyncio
async def test_docker_sandbox_cannot_see_outside_workspace(docker_ctx):
    """docker 模式只挂载工作区，宿主上工作区以外的文件看不到。"""
    outside = Path.home() / ".qzd-docker-tests" / "outside_secret.txt"
    outside.write_text("s3cret", encoding="utf-8")
    try:
        out = await make_run_python(docker_ctx)("print(open('../outside_secret.txt').read())")
    finally:
        outside.unlink(missing_ok=True)
    assert "s3cret" not in out["output"]
