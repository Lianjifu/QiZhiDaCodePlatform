"""agent 工具层测试：路径越界防护、文件读写、沙箱执行。"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.agents.tools.code import build_code_tools
from app.agents.tools.fs import build_fs_tools
from app.agents.tools.paths import WorkspaceError, resolve_in_workspace
from app.core.config import settings

# ---------- 路径安全 ----------


def test_relative_path_resolves_inside_workspace(tmp_path):
    target = resolve_in_workspace(tmp_path, "src/main.py")
    assert target == (tmp_path.resolve() / "src" / "main.py")


def test_parent_traversal_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(tmp_path, "../secret.txt")


def test_deep_traversal_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(tmp_path, "a/b/../../../../etc/passwd")


def test_absolute_path_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(tmp_path, "/etc/passwd")


def test_symlink_escape_is_rejected(tmp_path):
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s3cret", encoding="utf-8")
    (workspace / "link").symlink_to(outside)

    with pytest.raises(WorkspaceError):
        resolve_in_workspace(workspace, "link/secret.txt")


# ---------- 文件工具 ----------


def test_write_then_read_file(tmp_path):
    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    written = tools["write_file"].invoke({"path": "a/b.txt", "content": "hello"})
    assert "已写入" in written

    read = tools["read_file"].invoke({"path": "a/b.txt"})
    assert read == "hello"


def test_read_missing_file_returns_message(tmp_path):
    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    assert "文件不存在" in tools["read_file"].invoke({"path": "nope.txt"})


def test_list_files_shows_entries(tmp_path):
    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    tools["write_file"].invoke({"path": "x.txt", "content": "1"})
    listing = tools["list_files"].invoke({"path": "."})
    assert "x.txt" in listing


def test_file_tool_cannot_escape_workspace(tmp_path):
    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    with pytest.raises(WorkspaceError):
        tools["write_file"].invoke({"path": "../evil.txt", "content": "boom"})


# ---------- 代码执行 ----------


@pytest.mark.asyncio
async def test_run_python_executes_code(tmp_path):
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    result = await tools["run_python"].ainvoke({"code": "print('hi from sandbox')"})
    assert "退出码: 0" in result
    assert "hi from sandbox" in result


@pytest.mark.asyncio
async def test_run_python_reports_nonzero_exit(tmp_path):
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    result = await tools["run_python"].ainvoke({"code": "raise SystemExit(3)"})
    assert "退出码: 3" in result


@pytest.mark.asyncio
async def test_run_python_can_read_workspace_files(tmp_path):
    (tmp_path / "data.txt").write_text("42", encoding="utf-8")
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    result = await tools["run_python"].ainvoke({"code": "print(open('data.txt').read().strip())"})
    assert "42" in result


# ---------- docker 沙箱（需要本机有 docker，跳过不影响主流程） ----------


@pytest.fixture
def docker_workspace():
    """家目录下的临时工作区 —— macOS 的 Docker Desktop 只共享 /Users。

    挂在 /tmp 下容器会看到一个空目录，所以这里必须放在家目录里。
    """
    path = Path(tempfile.mkdtemp(prefix="qzd-ds-", dir=Path.home()))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def docker_sandbox(monkeypatch, docker_workspace):
    """切到 docker 沙箱模式，返回该工作区下的工具集。"""
    monkeypatch.setattr(settings, "sandbox_type", "docker")
    monkeypatch.setattr(settings, "sandbox_image", "python:3.12-slim")
    return {t.name: t for t in build_code_tools(docker_workspace)}


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("docker") is None, reason="本机没有 docker")
async def test_docker_sandbox_executes_code(docker_sandbox):
    result = await docker_sandbox["run_python"].ainvoke({"code": "print('docker ok')"})
    assert "退出码: 0" in result
    assert "docker ok" in result


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("docker") is None, reason="本机没有 docker")
async def test_docker_sandbox_has_no_network(docker_sandbox):
    result = await docker_sandbox["run_python"].ainvoke(
        {"code": "import socket;socket.create_connection(('1.1.1.1',53))"}
    )
    assert "退出码: 0" not in result


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("docker") is None, reason="本机没有 docker")
async def test_docker_sandbox_reports_unshared_workspace(monkeypatch):
    # 未共享的目录（macOS 的 /var/folders）应给出可读的提示，而不是裸的 file not found
    workspace = Path(tempfile.mkdtemp(prefix="qzd-unshared-"))
    try:
        monkeypatch.setattr(settings, "sandbox_type", "docker")
        monkeypatch.setattr(settings, "sandbox_image", "python:3.12-slim")
        tools = {t.name: t for t in build_code_tools(workspace)}
        result = await tools["run_python"].ainvoke({"code": "print('x')"})
        assert "提示" in result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
