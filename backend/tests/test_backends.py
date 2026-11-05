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
from deepagents.backends.composite import CompositeBackend

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
    """后端必须被 deepAgents 认作"支持执行命令"。

    deepAgents 的 supports_execution() 用 isinstance 判断 —— 传进去的后端
    认不出能执行，execute 工具会直接报"后端不支持执行"。
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


# ---------------------------------------------------------------- 产物目录


def test_平台后端是复合后端的子类(tmp_path):
    """必须是**子类**，不是包一层。

    框架多处用 ``isinstance(backend, CompositeBackend)`` 决定产物目录的落点
    （``middleware/filesystem.py:1993``、``middleware/summarization.py:739``）。
    包一层 → 判定为 False → 产物目录退化成 ``"/"`` → 落进用户工作区。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    assert isinstance(backend, CompositeBackend)


def test_产物目录落在工作区外面(tmp_path):
    """被淘汰的对话历史不能落进用户的工作区。

    框架那行判定：``artifacts_root = backend.artifacts_root
    if isinstance(backend, CompositeBackend) else "/"``。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    root = backend.artifacts_root if isinstance(backend, CompositeBackend) else "/"
    assert root == ARTIFACTS_ROUTE, f"产物目录退化了：{root}"

    backend.write(f"{root}/conversation_history/abc.md", "被淘汰的对话历史")

    inside = list(workspace.rglob("conversation_history"))
    assert not inside, f"对话历史落进工作区了：{inside}"
    outside = list((tmp_path / ".artifacts").rglob("abc.md"))
    assert outside, "对话历史没落在产物目录里"


def test_框架的文件中间件把历史外置到工作区外面(tmp_path):
    """走框架**自己**的判定 —— 不是我们复述一遍那行 isinstance。

    ``FilesystemMiddleware`` 算前缀的逻辑是
    ``middleware/filesystem.py:1993``，它决定被淘汰的对话历史写到哪儿。
    """
    from deepagents.middleware.filesystem import FilesystemMiddleware

    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)

    middleware = FilesystemMiddleware(backend=backend)
    prefix = middleware._conversation_history_prefix
    assert prefix.startswith(ARTIFACTS_ROUTE), f"历史外置的落点不对：{prefix}"


# ---------------------------------------------------------------- docker 模式


def test_docker_模式下_glob_与_grep_走得通(tmp_path):
    """docker 模式下 glob / grep 不能报错 —— 结果由宿主后端给出。

    默认后端是沙箱（容器里没有平台的文件树，返回空），路由 ``"/"`` 是宿主
    ``FilesystemBackend``（真实的工作区）。两个合并，命中的是宿主那份。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("needle here\n", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.sandbox_type = "docker"
    backend = build_backend(context, None)

    paths = [m["path"] for m in backend.glob("**/*").matches or []]
    assert paths == ["/a.py"], f"glob 不通或结果不对：{paths}"

    hits = [m["path"] for m in backend.grep("needle").matches or []]
    assert hits == ["/a.py"], f"grep 不通或结果不对：{hits}"


async def test_docker_模式下_异步_glob_与_grep_走得通(tmp_path):
    """agent 走的正是异步那条路。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("needle here\n", encoding="utf-8")

    context = ExecutionContext.build(workspace)
    context.sandbox_type = "docker"
    backend = build_backend(context, None)

    paths = [m["path"] for m in (await backend.aglob("**/*")).matches or []]
    assert paths == ["/a.py"], f"异步 glob 不通或结果不对：{paths}"

    hits = [m["path"] for m in (await backend.agrep("needle")).matches or []]
    assert hits == ["/a.py"], f"异步 grep 不通或结果不对：{hits}"


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


def test_sandbox_mutation_methods_raise(tmp_path):
    """DockerSandbox 改单个文件的操作必须报错，不能静默给"文件不存在"。

    基类的文件方法把虚拟路径原样塞进容器命令（/a.txt），而我们的工作区挂在
    /workspace —— 那个路径在容器里不存在，基类会返回看起来像正常结果的错误。

    **异步版本同样要报错** —— 基类的 ``aread`` / ``awrite`` / ``aedit`` 会在
    容器里真的跑命令，而 agent 走的正是异步那条路。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-bound")

    for call in (
        lambda: sandbox.read("/a.txt"),
        lambda: sandbox.write("/a.txt", "x"),
        lambda: sandbox.edit("/a.txt", "a", "b"),
        lambda: sandbox.delete("/a.txt"),
    ):
        with pytest.raises(NotImplementedError):
            call()


async def test_sandbox_async_mutation_methods_raise(tmp_path):
    """异步的改文件操作也要响亮地炸 —— 不能在容器里跑命令。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-bound-async")

    for call in (
        sandbox.aread("/a.txt"),
        sandbox.awrite("/a.txt", "x"),
        sandbox.aedit("/a.txt", "a", "b"),
        sandbox.adelete("/a.txt"),
    ):
        with pytest.raises(NotImplementedError):
            await call


def test_sandbox_search_methods_return_empty(tmp_path):
    """查找类返回**空结果**，不报错。

    容器里的文件树不是平台的文件树（工作区挂在 /workspace，平台的读写走宿主
    后端）。基类会拿虚拟路径去容器里跑 ls / grep / find —— 搜出来的是容器的
    系统文件，不是工作区内容。

    返回空还有个好处：``CompositeBackend`` 会把默认后端与各路由的结果**合并**，
    空结果不影响宿主后端给出的真实命中；报错则会让整个 glob / grep 一起失败。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-search")

    assert sandbox.ls("/").entries == []
    assert sandbox.glob("*").matches == []
    assert sandbox.grep("x").matches == []


async def test_sandbox_async_search_methods_return_empty(tmp_path):
    """异步的查找同样返回空 —— 更重要的是**不在容器里跑命令**。

    基类的 ``agrep`` / ``aglob`` / ``als`` 会真的调 ``aexecute``。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-search-async")

    def explode(*args, **kwargs):
        raise AssertionError("查找操作不该在容器里跑命令")

    monkey = sandbox.aexecute
    sandbox.aexecute = explode  # type: ignore[method-assign]
    try:
        assert (await sandbox.als("/")).entries == []
        assert (await sandbox.aglob("*")).matches == []
        assert (await sandbox.agrep("x")).matches == []
    finally:
        sandbox.aexecute = monkey  # type: ignore[method-assign]


def test_sandbox_grep_accepts_framework_call_shape(tmp_path):
    """框架按三个位置参数调 grep —— 签名对不上就整条 grep 报废。

    ``CompositeBackend._grep_backend`` 调用 ``backend.grep(pattern, path, glob)``，
    可能再带 ``max_count``。旧签名只收两个位置参数，第三个挤到 ``glob`` 上 →
    ``TypeError``。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = DockerSandbox(workspace, session_id="t-signature")

    result = sandbox.grep("pattern", None, "*.py", max_count=10)
    assert result.matches == []
    assert result.error is None


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
