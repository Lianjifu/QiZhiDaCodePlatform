"""内置能力测试：路径安全、文件读写改查移拷、glob/grep、沙箱执行、网络工具。

工具形态参考 Claude Code：读 / 写 / 改 / 移 / 拷 / 建目录 / 切目录 / 列 / glob /
grep / 执行 / 抓取 / 搜索。

能力现在返回**结构化结果**（dict，`content` 是给模型看的文本），失败抛
`CapabilityError` —— 不再返回"看起来像成功"的字符串。
"""

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.fs import (
    make_change_directory,
    make_copy_file,
    make_edit_file,
    make_glob,
    make_grep,
    make_list_files,
    make_make_dir,
    make_move_file,
    make_read_file,
    make_write_file,
)
from app.capabilities.registration.builtin.paths import WorkspaceError, resolve_in_workspace
from app.capabilities.registration.builtin.sandbox import (
    exec_command,
    make_run_bash,
    make_run_python,
)
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
    assert "1\t第一行" in out["content"]
    assert "3\t第三行" in out["content"]
    assert out["line_count"] == 3


def test_read_file_supports_offset_and_limit(ctx):
    (ctx.workspace / "a.txt").write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    out = make_read_file(ctx)("a.txt", offset=2, limit=3)
    assert "line2" in out["content"] and "line4" in out["content"]
    assert "line5" not in out["content"]
    assert out["truncated"] is True


def test_read_missing_file_raises(ctx):
    with pytest.raises(CapabilityError) as exc:
        make_read_file(ctx)("nope.txt")
    assert "文件不存在" in exc.value.message
    assert exc.value.code == ErrorCode.INVALID_ARGUMENTS


def test_read_file_cannot_escape_workspace(ctx):
    with pytest.raises(CapabilityError) as exc:
        make_read_file(ctx)("../evil.txt")
    assert exc.value.code == ErrorCode.PERMISSION_DENIED


# ---------- 文件：写 ----------


def test_write_then_read(ctx):
    assert "创建" in make_write_file(ctx)("a/b.txt", "hello")["content"]
    assert make_read_file(ctx)("a/b.txt")["content"].endswith("hello")


def test_write_overwrites_existing(ctx):
    make_write_file(ctx)("a.txt", "第一版")
    assert "覆盖" in make_write_file(ctx)("a.txt", "第二版")["content"]
    assert "第二版" in make_read_file(ctx)("a.txt")["content"]


def test_write_reports_created_flag(ctx):
    assert make_write_file(ctx)("a.txt", "x")["created"] is True
    assert make_write_file(ctx)("a.txt", "y")["created"] is False


# ---------- 文件：改 ----------


def test_edit_file_replaces_unique_match(ctx):
    (ctx.workspace / "a.txt").write_text("hello world", encoding="utf-8")
    out = make_edit_file(ctx)("a.txt", "world", "there")
    assert "已替换" in out["content"]
    assert out["replaced_count"] == 1
    assert "hello there" in (ctx.workspace / "a.txt").read_text(encoding="utf-8")


def test_edit_file_refuses_ambiguous_match(ctx):
    (ctx.workspace / "a.txt").write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        make_edit_file(ctx)("a.txt", "x", "y")
    assert "出现了 2 次" in exc.value.message


def test_edit_file_replace_all(ctx):
    (ctx.workspace / "a.txt").write_text("x\nx\n", encoding="utf-8")
    make_edit_file(ctx)("a.txt", "x", "y", replace_all=True)
    assert (ctx.workspace / "a.txt").read_text(encoding="utf-8") == "y\ny\n"


def test_edit_file_reports_missing_text(ctx):
    (ctx.workspace / "a.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        make_edit_file(ctx)("a.txt", "nope", "y")
    assert "没有找到" in exc.value.message


# ---------- 文件：移动 / 复制 / 建目录 ----------


def test_make_dir_creates_nested_directories(ctx):
    out = make_make_dir(ctx)("a/b/c")
    assert "已创建" in out["content"]
    assert (ctx.workspace / "a" / "b" / "c").is_dir()


def test_make_dir_on_existing_directory_is_ok(ctx):
    (ctx.workspace / "a").mkdir()
    assert "已存在" in make_make_dir(ctx)("a")["content"]


def test_move_file_moves_file(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    out = make_move_file(ctx)("a.txt", "b.txt")
    assert "已移动" in out["content"]
    assert not (ctx.workspace / "a.txt").exists()
    assert (ctx.workspace / "b.txt").read_text(encoding="utf-8") == "x"


def test_move_file_into_existing_directory(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    (ctx.workspace / "sub").mkdir()
    make_move_file(ctx)("a.txt", "sub")
    assert (ctx.workspace / "sub" / "a.txt").exists()


def test_move_file_creates_missing_parent(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    make_move_file(ctx)("a.txt", "out/b.txt")
    assert (ctx.workspace / "out" / "b.txt").exists()


def test_move_file_refuses_to_overwrite(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    (ctx.workspace / "b.txt").write_text("y", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        make_move_file(ctx)("a.txt", "b.txt")
    assert "已存在" in exc.value.message
    assert (ctx.workspace / "b.txt").read_text(encoding="utf-8") == "y"


def test_move_file_reports_missing_source(ctx):
    with pytest.raises(CapabilityError) as exc:
        make_move_file(ctx)("nope.txt", "b.txt")
    assert "源不存在" in exc.value.message


def test_move_file_cannot_escape_workspace(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CapabilityError):
        make_move_file(ctx)("a.txt", "../escaped.txt")


def test_copy_file_copies_file(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    out = make_copy_file(ctx)("a.txt", "b.txt")
    assert "已复制" in out["content"]
    assert (ctx.workspace / "a.txt").exists()
    assert (ctx.workspace / "b.txt").read_text(encoding="utf-8") == "x"


def test_copy_file_copies_directory(ctx):
    (ctx.workspace / "src").mkdir()
    (ctx.workspace / "src" / "a.txt").write_text("x", encoding="utf-8")
    make_copy_file(ctx)("src", "dst")
    assert (ctx.workspace / "dst" / "a.txt").read_text(encoding="utf-8") == "x"


def test_copy_file_refuses_to_overwrite(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    (ctx.workspace / "b.txt").write_text("y", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        make_copy_file(ctx)("a.txt", "b.txt")
    assert "已存在" in exc.value.message
    assert (ctx.workspace / "b.txt").read_text(encoding="utf-8") == "y"


# ---------- 当前目录（cd） ----------


def test_change_directory_switches_relative_base(ctx):
    (ctx.workspace / "src").mkdir()
    (ctx.workspace / "src" / "a.txt").write_text("x", encoding="utf-8")
    out = make_change_directory(ctx)("src")
    assert "已切换" in out["content"]
    # 切换之后，相对路径基于新目录
    assert "1\tx" in make_read_file(ctx)("a.txt")["content"]
    # "~" 回到工作区根目录，就看不到了
    make_change_directory(ctx)("~")
    assert "文件不存在" in _read_error_message(ctx, "a.txt")


def test_change_directory_dot_is_current_directory(ctx):
    (ctx.workspace / "src").mkdir()
    make_change_directory(ctx)("src")
    make_change_directory(ctx)(".")
    assert ctx.workdir == (ctx.workspace / "src").resolve()


def test_change_directory_rejects_escape_from_root(ctx):
    with pytest.raises(CapabilityError):
        make_change_directory(ctx)("..")


def test_change_directory_allows_going_up_inside_workspace(ctx):
    (ctx.workspace / "src").mkdir()
    make_change_directory(ctx)("src")
    assert "已切换" in make_change_directory(ctx)("..")["content"]


def test_change_directory_reports_missing_dir(ctx):
    with pytest.raises(CapabilityError) as exc:
        make_change_directory(ctx)("nope")
    assert "目录不存在" in exc.value.message


def test_change_directory_rejects_file(ctx):
    (ctx.workspace / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        make_change_directory(ctx)("a.txt")
    assert "不是目录" in exc.value.message


def test_current_directory_persists_across_contexts(tmp_path):
    """同一会话的多次请求之间，当前目录要保持。"""
    (tmp_path / "src").mkdir()
    first = ExecutionContext.build(workspace=tmp_path)
    make_change_directory(first)("src")
    assert first.workdir == (tmp_path / "src").resolve()

    second = ExecutionContext.build(workspace=tmp_path)
    assert second.workdir == (tmp_path / "src").resolve()


def test_current_directory_falls_back_when_directory_is_gone(tmp_path):
    (tmp_path / "src").mkdir()
    first = ExecutionContext.build(workspace=tmp_path)
    make_change_directory(first)("src")
    shutil.rmtree(tmp_path / "src")
    second = ExecutionContext.build(workspace=tmp_path)
    assert second.workdir == tmp_path.resolve()


def test_state_file_lives_outside_workspace(tmp_path):
    """状态文件不能落在工作区里，否则模型的文件工具会看见它。"""
    (tmp_path / "src").mkdir()
    context = ExecutionContext.build(workspace=tmp_path)
    make_change_directory(context)("src")
    assert not (tmp_path / ".qzd_state").exists()
    assert (tmp_path.parent / ".qzd_state").is_dir()


# ---------- 文件：列 / glob / grep ----------


def test_list_files(ctx):
    (ctx.workspace / "x.txt").write_text("1", encoding="utf-8")
    (ctx.workspace / "sub").mkdir()
    out = make_list_files(ctx)(".")
    assert "x.txt" in out["content"] and "sub/" in out["content"]
    assert {entry["name"] for entry in out["entries"]} == {"x.txt", "sub"}


def test_glob_finds_by_pattern(ctx):
    (ctx.workspace / "src").mkdir()
    (ctx.workspace / "src" / "a.py").write_text("x", encoding="utf-8")
    (ctx.workspace / "src" / "b.txt").write_text("x", encoding="utf-8")
    out = make_glob(ctx)("**/*.py")
    assert "src/a.py" in out["matches"]
    assert "b.txt" not in out["matches"]


def test_grep_finds_content(ctx):
    (ctx.workspace / "a.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    out = make_grep(ctx)("import os")
    assert "a.py:1" in out["content"]
    assert out["matches"][0]["file"] == "a.py"


def test_grep_reports_no_match(ctx):
    (ctx.workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert "没有匹配" in make_grep(ctx)("nothing-here")["content"]


# ---------- 沙箱执行 ----------


@pytest.mark.asyncio
async def test_run_python_executes_code(ctx):
    out = await make_run_python(ctx)("print('hi from sandbox')")
    assert out["exit_code"] == 0
    assert "hi from sandbox" in out["stdout"]
    assert "退出码: 0" in out["content"]


@pytest.mark.asyncio
async def test_run_python_reports_nonzero_exit(ctx):
    out = await make_run_python(ctx)("raise SystemExit(3)")
    assert out["exit_code"] == 3


@pytest.mark.asyncio
async def test_run_python_can_read_workspace_files(ctx):
    (ctx.workspace / "data.txt").write_text("42", encoding="utf-8")
    out = await make_run_python(ctx)("print(open('data.txt').read().strip())")
    assert "42" in out["stdout"]


@pytest.mark.asyncio
async def test_run_bash_executes_command(ctx):
    out = await make_run_bash(ctx)("echo hello-bash")
    assert out["exit_code"] == 0
    assert "hello-bash" in out["stdout"]


@pytest.mark.asyncio
async def test_run_bash_reports_nonzero_exit(ctx):
    out = await make_run_bash(ctx)("exit 7")
    assert out["exit_code"] == 7


@pytest.mark.asyncio
async def test_local_sandbox_is_not_isolated(ctx, tmp_path):
    """local 模式没有隔离 —— 这是它只适合开发用的原因。

    隔离是 docker 模式的属性；local 模式下 agent 的代码能访问宿主机上
    工作区以外的任何文件。这里把这条事实固定下来，避免误以为 local 也是沙箱。
    """
    secret = tmp_path.parent / "outside_secret.txt"
    secret.write_text("s3cret", encoding="utf-8")
    out = await make_run_bash(ctx)("cat ../outside_secret.txt 2>&1 || echo '看不到'")
    assert "s3cret" in out["stdout"]


@pytest.mark.asyncio
async def test_run_bash_runs_in_current_directory(ctx):
    (ctx.workspace / "src").mkdir()
    make_change_directory(ctx)("src")
    out = await make_run_bash(ctx)("pwd")
    assert "src" in out["stdout"]


@pytest.mark.asyncio
async def test_run_python_runs_in_current_directory(ctx):
    (ctx.workspace / "src").mkdir()
    make_change_directory(ctx)("src")
    out = await make_run_python(ctx)("import os;print(os.getcwd())")
    assert "src" in out["stdout"]


# ---------- 网络 ----------


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
    assert "docker ok" in out["stdout"]


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
        out = await make_run_bash(docker_ctx)("cat ../outside_secret.txt 2>&1 || echo '看不到'")
    finally:
        outside.unlink(missing_ok=True)
    assert "s3cret" not in out["stdout"]


# ---------- 辅助 ----------


def _read_error_message(ctx, path: str) -> str:
    """读文件失败时的错误信息。"""
    try:
        make_read_file(ctx)(path)
    except CapabilityError as exc:
        return exc.message
    return ""
