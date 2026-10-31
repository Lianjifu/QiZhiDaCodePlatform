"""内置能力测试：路径安全、文件读写改查、glob/grep、沙箱执行、网络工具。

工具形态参考 Claude Code：读 / 写 / 改 / 列 / glob / grep / 执行 / 抓取 / 搜索。
"""

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.capabilities.builtin.fs import (
    make_edit_file,
    make_glob,
    make_grep,
    make_list_files,
    make_read_file,
    make_write_file,
)
from app.capabilities.builtin.paths import WorkspaceError, resolve_in_workspace
from app.capabilities.builtin.sandbox import (
    exec_command,
    make_run_bash,
    make_run_python,
)
from app.capabilities.builtin.web import _html_to_text, make_web_fetch, make_web_search
from app.capabilities.context import ExecutionContext


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

    (workspace / "probe.txt").write_text("probe", encoding="utf-8")
    exit_code, stdout, _ = await exec_command("cat probe.txt", context)
    if "probe" not in stdout:
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


# ---------- 路径安全 ----------


def test_relative_path_resolves_inside_workspace(tmp_path):
    assert resolve_in_workspace(tmp_path, "src/main.py") == (tmp_path.resolve() / "src" / "main.py")


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


# ---------- 文件：读 ----------


def test_read_file_returns_numbered_lines(ctx):
    (ctx.workspace / "a.txt").write_text("第一行\n第二行\n第三行", encoding="utf-8")
    out = make_read_file(ctx)("a.txt")
    assert "1\t第一行" in out
    assert "3\t第三行" in out


def test_read_file_supports_offset_and_limit(ctx):
    (ctx.workspace / "a.txt").write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    out = make_read_file(ctx)("a.txt", offset=2, limit=3)
    assert "line2" in out and "line4" in out
    assert "line5" not in out


def test_read_missing_file_returns_message(ctx):
    assert "文件不存在" in make_read_file(ctx)("nope.txt")


def test_read_file_cannot_escape_workspace(ctx):
    with pytest.raises(WorkspaceError):
        make_read_file(ctx)("../evil.txt")


# ---------- 文件：写 ----------


def test_write_then_read(ctx):
    assert "创建" in make_write_file(ctx)("a/b.txt", "hello")
    assert make_read_file(ctx)("a/b.txt").endswith("hello")


def test_write_overwrites_existing(ctx):
    make_write_file(ctx)("a.txt", "第一版")
    assert "覆盖" in make_write_file(ctx)("a.txt", "第二版")
    assert "第二版" in make_read_file(ctx)("a.txt")


# ---------- 文件：改 ----------


def test_edit_file_replaces_unique_match(ctx):
    (ctx.workspace / "a.txt").write_text("hello world", encoding="utf-8")
    out = make_edit_file(ctx)("a.txt", "world", "there")
    assert "已替换" in out
    assert "hello there" in (ctx.workspace / "a.txt").read_text(encoding="utf-8")


def test_edit_file_refuses_ambiguous_match(ctx):
    (ctx.workspace / "a.txt").write_text("x\nx\n", encoding="utf-8")
    out = make_edit_file(ctx)("a.txt", "x", "y")
    assert "出现了 2 次" in out


def test_edit_file_replace_all(ctx):
    (ctx.workspace / "a.txt").write_text("x\nx\n", encoding="utf-8")
    make_edit_file(ctx)("a.txt", "x", "y", replace_all=True)
    assert (ctx.workspace / "a.txt").read_text(encoding="utf-8") == "y\ny\n"


def test_edit_file_reports_missing_text(ctx):
    (ctx.workspace / "a.txt").write_text("hello", encoding="utf-8")
    assert "没有找到" in make_edit_file(ctx)("a.txt", "nope", "y")


# ---------- 文件：列 / glob / grep ----------


def test_list_files(ctx):
    (ctx.workspace / "x.txt").write_text("1", encoding="utf-8")
    (ctx.workspace / "sub").mkdir()
    out = make_list_files(ctx)(".")
    assert "x.txt" in out and "sub/" in out


def test_glob_finds_by_pattern(ctx):
    (ctx.workspace / "src").mkdir()
    (ctx.workspace / "src" / "a.py").write_text("x", encoding="utf-8")
    (ctx.workspace / "src" / "b.txt").write_text("x", encoding="utf-8")
    out = make_glob(ctx)("**/*.py")
    assert "src/a.py" in out
    assert "b.txt" not in out


def test_grep_finds_content(ctx):
    (ctx.workspace / "a.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    out = make_grep(ctx)("import os")
    assert "a.py:1" in out


def test_grep_reports_no_match(ctx):
    (ctx.workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert "没有匹配" in make_grep(ctx)("nothing-here")


# ---------- 沙箱执行 ----------


@pytest.mark.asyncio
async def test_run_python_executes_code(ctx):
    out = await make_run_python(ctx)("print('hi from sandbox')")
    assert "退出码: 0" in out
    assert "hi from sandbox" in out


@pytest.mark.asyncio
async def test_run_python_reports_nonzero_exit(ctx):
    out = await make_run_python(ctx)("raise SystemExit(3)")
    assert "退出码: 3" in out


@pytest.mark.asyncio
async def test_run_python_can_read_workspace_files(ctx):
    (ctx.workspace / "data.txt").write_text("42", encoding="utf-8")
    out = await make_run_python(ctx)("print(open('data.txt').read().strip())")
    assert "42" in out


@pytest.mark.asyncio
async def test_run_bash_executes_command(ctx):
    out = await make_run_bash(ctx)("echo hello-bash")
    assert "退出码: 0" in out
    assert "hello-bash" in out


@pytest.mark.asyncio
async def test_run_bash_reports_nonzero_exit(ctx):
    out = await make_run_bash(ctx)("exit 7")
    assert "退出码: 7" in out


@pytest.mark.asyncio
async def test_local_sandbox_is_not_isolated(ctx, tmp_path):
    """local 模式没有隔离 —— 这是它只适合开发用的原因。

    隔离是 docker 模式的属性；local 模式下 agent 的代码能访问宿主机上
    工作区以外的任何文件。这里把这条事实固定下来，避免误以为 local 也是沙箱。
    """
    secret = tmp_path.parent / "outside_secret.txt"
    secret.write_text("s3cret", encoding="utf-8")
    out = await make_run_bash(ctx)("cat ../outside_secret.txt 2>&1 || echo '看不到'")
    assert "s3cret" in out


# ---------- 网络 ----------


def test_html_to_text_strips_tags_and_scripts():
    raw = "<html><head><style>a{}</style><script>x()</script></head><body><p>你好</p></body></html>"
    text = _html_to_text(raw)
    assert "你好" in text
    assert "<" not in text
    assert "x()" not in text


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_url(ctx):
    out = await make_web_fetch(ctx)("file:///etc/passwd")
    assert "只支持 http/https" in out


@pytest.mark.asyncio
async def test_web_search_without_key_gives_actionable_hint(ctx, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.web_search_provider", "")
    monkeypatch.setattr("app.core.config.settings.web_search_api_key", "")
    out = await make_web_search(ctx)("langchain")
    assert "WEB_SEARCH_PROVIDER" in out


# ---------- docker 沙箱 ----------


@pytest.mark.asyncio
async def test_docker_sandbox_runs_python(docker_ctx):
    out = await make_run_python(docker_ctx)("print('docker ok')")
    assert "退出码: 0" in out
    assert "docker ok" in out


@pytest.mark.asyncio
async def test_docker_sandbox_has_no_network(docker_ctx):
    """docker 模式下必须断网（--network none），容器内任何出网请求都要失败。"""
    out = await make_run_python(docker_ctx)(
        "import socket;socket.create_connection(('1.1.1.1',53),timeout=3)"
    )
    assert "退出码: 0" not in out


@pytest.mark.asyncio
async def test_docker_sandbox_cannot_see_outside_workspace(docker_ctx):
    """docker 模式只挂载工作区，宿主上工作区以外的文件看不到。"""
    outside = Path.home() / ".qzd-docker-tests" / "outside_secret.txt"
    outside.write_text("s3cret", encoding="utf-8")
    try:
        out = await make_run_bash(docker_ctx)("cat ../outside_secret.txt 2>&1 || echo '看不到'")
    finally:
        outside.unlink(missing_ok=True)
    assert "s3cret" not in out
