"""后端存储的测试。

覆盖这几条最容易出问题的地方：

- **DockerSandbox 只提供 execute** —— 基类的文件方法路径空间是错的，
  误用必须立刻炸出来，不能静默返回"文件不存在"；
- **容器按会话复用** —— 装好的依赖、生成的文件跨调用保留；
- **ls("/") 要能看到子路由** —— 否则模型以为技能目录不存在；
- **路径隔离** —— 越界路径被拒。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.capabilities.backends import sandbox_backend
from app.capabilities.backends.assembly import ARTIFACTS_ROUTE, SKILLS_ROUTE, build_backend
from app.capabilities.backends.container_pool import (
    CONTAINER_PREFIX,
    ContainerPool,
    container_name,
)
from app.capabilities.backends.platform_backend import PlatformBackend
from app.capabilities.backends.sandbox_backend import DockerSandbox
from app.capabilities.execution.context import ExecutionContext

# ---------------------------------------------------------------- 路由


def test_ls_root_shows_sub_routes(tmp_path):
    """ls("/") 要能看到子路由。

    CompositeBackend 的 route "/" 命中后走单 backend 分支，看不到 /skills/
    与 /.artifacts/ —— 模型会以为技能目录不存在。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    (workspace / "main.py").write_text("x", encoding="utf-8")
    (skills / "SKILL.md").write_text("y", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.thread_id = "t-ls"
    backend = build_backend(context, skills)

    paths = {entry["path"] for entry in backend.ls("/").entries}
    assert "/main.py" in paths
    assert SKILLS_ROUTE in paths
    assert ARTIFACTS_ROUTE in paths


def test_ls_of_sub_route_unchanged(tmp_path):
    """列子路由本身时不该被补虚拟目录项。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("y", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.thread_id = "t-ls2"
    backend = build_backend(context, skills)

    paths = {entry["path"] for entry in backend.ls(SKILLS_ROUTE).entries}
    assert paths == {f"{SKILLS_ROUTE}SKILL.md"}


def test_backend_supports_execution(tmp_path):
    """包装层必须被 deepAgents 认作"支持执行命令"。

    deepAgents 的 supports_execution() 用 isinstance 判断 —— 只实现
    BackendProtocol 的话，execute 工具会直接报"后端不支持执行"。
    """
    from deepagents.middleware.filesystem import supports_execution

    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    assert supports_execution(backend)


def test_platform_backend_forwards_default_properties(tmp_path):
    """透传属性：路由表、默认后端、产物目录。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    context.thread_id = "t-fwd"
    backend = build_backend(context, None)

    assert isinstance(backend, PlatformBackend)
    assert backend.artifacts_root == ARTIFACTS_ROUTE
    assert "/" in backend.routes
    assert backend.default is not None


def test_glob_does_not_duplicate_files(tmp_path):
    """glob 不该把同一个文件列两次。

    CompositeBackend.glob 在 path 为 "/" 时搜**默认后端 + 所有路由**。我们的
    默认后端（沙箱）与 route "/"（宿主 FilesystemBackend）指向同一个工作区 ——
    不去重的话每个文件都出现两次。agent 用 glob 找产物时会看到一堆重复项。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("x", encoding="utf-8")
    (workspace / "b.md").write_text("y", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.thread_id = "t-glob"
    backend = build_backend(context, None)

    paths = [m["path"] for m in backend.glob("**/*").matches or []]
    assert paths == ["/a.py", "/b.md"], f"glob 结果重复了：{paths}"


def test_grep_does_not_duplicate_matches(tmp_path):
    """grep 同样要去重。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("needle here\n", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.thread_id = "t-grep"
    backend = build_backend(context, None)

    paths = [m["path"] for m in backend.grep("needle").matches or []]
    assert paths == ["/a.py"], f"grep 结果重复了：{paths}"


# ---------------------------------------------------------------- 路径隔离


def test_path_traversal_rejected(tmp_path):
    """越界路径被拒 —— 虚拟模式的路径安全由 FilesystemBackend 保证。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    with pytest.raises(ValueError):
        backend.read("/../../etc/passwd")


def test_file_tools_operate_in_workspace(tmp_path):
    """文件工具写的工作区，文件工具能读回来。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    backend.write("/a.txt", "hello")
    result = backend.read("/a.txt")
    assert result.file_data is not None
    assert result.file_data["content"] == "hello"


# ---------------------------------------------------------------- 沙箱边界


def test_sandbox_file_methods_raise(tmp_path):
    """DockerSandbox 的文件方法必须报错，不能静默给"文件不存在"。

    基类的文件方法把虚拟路径原样塞进容器命令（/a.txt），而我们的工作区挂在
    /workspace —— 那个路径在容器里不存在，基类会返回看起来像正常结果的错误。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-bound")

    with pytest.raises(NotImplementedError):
        sandbox.ls("/")
    with pytest.raises(NotImplementedError):
        sandbox.read("/a.txt")
    with pytest.raises(NotImplementedError):
        sandbox.write("/a.txt", "x")
    with pytest.raises(NotImplementedError):
        sandbox.edit("/a.txt", "a", "b")
    with pytest.raises(NotImplementedError):
        sandbox.delete("/a.txt")
    with pytest.raises(NotImplementedError):
        sandbox.grep("x")
    with pytest.raises(NotImplementedError):
        sandbox.glob("*")


def test_sandbox_execute_is_available(tmp_path):
    """execute 是这个后端唯一支持的操作 —— 它不该被误伤。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-exec")

    # 只是确认方法存在且没被覆写成报错
    assert callable(sandbox.execute)
    assert sandbox.id


# ---------------------------------------------------------------- 容器池


def test_container_name_is_docker_safe():
    """容器名要符合 docker 的命名规则。"""
    name = container_name("t/普通 会话 名")
    assert name.startswith(CONTAINER_PREFIX)
    assert all(ch.isalnum() or ch in "_.-" for ch in name)


def test_container_name_stable_per_session():
    """同一会话永远同一个容器名 —— 复用靠它。"""
    assert container_name("t-1") == container_name("t-1")
    assert container_name("t-1") != container_name("t-2")


def test_pool_tracks_idle():
    """池要能算出空闲时长。"""
    pool = ContainerPool(idle_timeout=1800)
    assert pool.idle_seconds("t-new") == float("inf")

    pool.touch("t-1")
    assert pool.idle_seconds("t-1") < 1.0


def test_pool_locks_are_per_session():
    """不同会话各有各的锁 —— 不同会话可以并行执行。"""
    pool = ContainerPool()
    assert pool.lock_for("t-1") is pool.lock_for("t-1")
    assert pool.lock_for("t-1") is not pool.lock_for("t-2")


def test_pool_reap_skips_fresh_sessions():
    """没空闲够的容器不动。"""
    pool = ContainerPool(idle_timeout=1800)
    pool.touch("t-fresh")
    # 不真的删容器（没有 docker），只验证筛选逻辑：新鲜的不会被选中
    removed = pool.reap_idle()
    assert removed == [] or "t-fresh" not in removed


@pytest.mark.skipif(shutil.which("docker") is None, reason="需要 docker 才能测容器生命周期")
def test_container_reuse_preserves_installed_packages(tmp_path):
    """容器复用后，装好的东西跨调用保留。

    这是 B2 的核心收益：agent 装完依赖，下一轮还能用。

    **工作区必须在 docker 的共享路径里** —— macOS 上 Colima 默认共享 ``$HOME``，
    ``tmp_path`` 在 ``/private/var`` 下，docker 看不到它（会当成容器内的新目录
    创建，宿主写的东西看不见）。所以这里用 ``$HOME`` 下的临时目录。
    """
    from app.capabilities.backends.container_pool import remove_container

    workspace = Path(tempfile.mkdtemp(dir=Path.home()))
    session = "t-reuse"
    name = container_name(session)
    sandbox = DockerSandbox(workspace, session_id=session)
    try:
        script = "mkdir -p /workspace/mylib && printf 'VALUE=42' > /workspace/mylib/mod.py"
        first = sandbox.execute(script)
        assert first.exit_code == 0, first.output

        second = sandbox.execute("cat /workspace/mylib/mod.py")
        assert second.exit_code == 0, second.output
        assert "VALUE=42" in second.output
    finally:
        remove_container(name)
        shutil.rmtree(workspace, ignore_errors=True)


# ---------------------------------------------------------------- 沙箱提速


def test_挂载预检按工作区与镜像只做一次(monkeypatch, tmp_path):
    """挂载预检不该每次命令都做一遍 —— 也不该为此另起容器。

    预检现在在会话容器里做（``docker exec … test -f``），所以**一次
    一次性容器都不该起**（``docker run --rm``）。以前预检要起一个
    一次性容器，验完就删，紧接着还要再起一个常驻容器 —— 两次容器创建。
    """
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox_backend, "_MOUNT_OK", set())

    workspace = tmp_path / "ws"
    workspace.mkdir()
    for _ in range(3):
        DockerSandbox(workspace, session_id="t-mount").execute("echo hi")

    assert not [argv for argv in calls if "--rm" in argv], "不该为预检起一次性容器"
    checks = [argv for argv in calls if "test" in argv and "-f" in argv]
    assert len(checks) == 1, f"预检跑了 {len(checks)} 次，应该只跑一次"


def test_探活结果短时间复用(monkeypatch, tmp_path):
    """探活是一次 docker inspect（三十毫秒）—— 连续几条命令不该各探一次。"""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, stdout="true|/ws", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox_backend, "_MOUNT_OK", {("dummy", "dummy")})
    pool = ContainerPool()
    monkeypatch.setattr(sandbox_backend, "get_pool", lambda: pool)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-probe")
    for _ in range(3):
        sandbox.execute("echo hi")

    probes = [argv for argv in calls if argv[:2] == ["docker", "inspect"]]
    assert len(probes) <= 1, f"探活跑了 {len(probes)} 次，连续调用应该只探一次"


def test_容器没了的报错认得出来():
    """认错了会把命令跑两遍 —— 副作用也就跑两遍。"""
    from app.capabilities.backends.sandbox_backend import _container_gone

    def done(code: int, stderr: str):
        return subprocess.CompletedProcess(["docker"], code, stdout="", stderr=stderr)

    # docker daemon 自己的报错 → 容器没了
    assert _container_gone(done(1, "Error response from daemon: No such container: x"))
    assert _container_gone(done(1, "Error response from daemon: Container x is not running"))
    # **命令自己打的那句话不算** —— 认错了命令就跑两遍
    assert not _container_gone(done(1, "No such container"))
    assert not _container_gone(done(2, "command not found"))
