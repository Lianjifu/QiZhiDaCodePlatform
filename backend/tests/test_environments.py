"""工作目录（环境）与 git 能力的测试。

用**真的 git**跑 —— worktree、分支、合并这些东西用 mock 测不出真问题。
宿主机没有 git 就跳过（跟 ``test_backends.py`` 里对 docker 的处理一样）。
"""

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.environments.naming import slugify, validate_branch
from app.environments.paths import is_allowed, repo_roots, require_allowed
from app.environments.service import (
    EnvironmentService,
    clear_environment_cache,
    invalidate_environment_cache,
)
from app.git.service import GitService

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git 才能测")


def _init_repo(path: Path) -> Path:
    """在 ``path`` 建一个有一笔提交的 git 仓库。"""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """白名单根目录（里面有一个仓库）。"""
    root = tmp_path / "allowed"
    root.mkdir()
    _init_repo(root / "demo")
    monkeypatch.setattr(settings, "repo_roots", str(root))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    # worktree 的落点也要挪进临时目录 —— 否则测试会动到真实的 worktree 根
    monkeypatch.setattr(settings, "worktree_root", str(tmp_path / "worktrees"))
    return root


@pytest.fixture(autouse=True)
def _清环境缓存():
    """环境清单的缓存是**模块级**的 —— 每个用例前后都**整个丢掉**。

    白名单每个用例都不一样（临时目录），缓存不清的话就会串。
    """
    clear_environment_cache()
    yield
    clear_environment_cache()


# ---------- 一次问清楚 ----------


@needs_git
def test_一次问出来仓库信息(roots):
    """``repo_info`` 一次调用拿齐三件事 —— 扫目录时每个目录只问一次。"""
    repo = roots / "demo"
    info = GitService(repo).repo_info()
    assert info is not None
    assert info.is_inside_work_tree is True
    assert info.branch == "main"
    assert Path(info.common_dir).name == ".git"
    # common dir 的父目录才是仓库根
    assert Path(info.common_dir).parent == repo.resolve()


@needs_git
def test_不是仓库就没有信息(tmp_path):
    assert GitService(tmp_path).repo_info() is None


@needs_git
def test_只问脏不脏不用起三个进程(roots):
    repo = roots / "demo"
    assert GitService(repo).is_dirty() is False
    (repo / "README.md").write_text("# 改了\n", encoding="utf-8")
    assert GitService(repo).is_dirty() is True


# ---------- 清单的缓存 ----------


@needs_git
def test_清单有缓存_短时间内不重扫(roots, monkeypatch):
    """扫一遍要一秒多 —— 短时间内重复问不该再扫。"""
    service = EnvironmentService()
    calls: list[int] = []
    original = service._scan_environments

    def counted() -> list:
        calls.append(1)
        return original()

    monkeypatch.setattr(service, "_scan_environments", counted)
    service.list_environments()
    service.list_environments()
    assert len(calls) == 1, f"扫了 {len(calls)} 次，应该只扫一次"


@needs_git
def test_缓存过期后先给旧数据_后台再刷(roots, monkeypatch):
    """过期了也别让用户干等 —— 先给旧的，后台重扫。

    改了环境（新建/删掉 worktree）之后也是这条路：旧数据立刻到手，
    后台刷完下一轮就是新的。
    """
    service = EnvironmentService()
    calls: list[int] = []
    scanned = threading.Event()
    original = service._scan_environments

    def counted() -> list:
        calls.append(1)
        # **扫得慢一点** —— 不然后台刷新可能一瞬间就完了，看不出"没等"
        time.sleep(0.3)
        try:
            return original()
        finally:
            scanned.set()

    monkeypatch.setattr(service, "_scan_environments", counted)
    service.list_environments()
    assert len(calls) == 1

    # 标记过期 → 下一次读应该立刻返回旧数据，并在后台重扫
    invalidate_environment_cache()
    scanned.clear()
    started = time.monotonic()
    assert service.list_environments()  # 立刻就拿到了
    elapsed = time.monotonic() - started
    # **关键**：返回的时候后台那次扫描还在跑（它要睡 0.3 秒）——
    # 说明调用方根本没等它。
    assert elapsed < 0.2, f"等了 {elapsed:.2f}s 才返回 —— 那就等于没在后台刷"

    assert scanned.wait(timeout=10), "后台刷新没跑"
    assert len(calls) == 2, f"后台应该扫了一次，实际 {len(calls)} 次"


# ---------- 白名单 ----------


def test_白名单外的目录不能当工作目录(tmp_path, monkeypatch):
    """平台没有鉴权，白名单是唯一的闸门 —— 之外的目录一律拒绝。"""
    monkeypatch.setattr(settings, "repo_roots", str(tmp_path / "allowed"))
    outside = tmp_path / "secret"
    outside.mkdir()
    assert is_allowed(outside) is False
    with pytest.raises(ValidationError):
        require_allowed(outside)


def test_没配白名单就一个都不许(roots, monkeypatch):
    """默认配置是「全禁」，不是「全开」。"""
    monkeypatch.setattr(settings, "repo_roots", "")
    assert repo_roots() == []
    assert is_allowed(roots / "demo") is False


def test_软链接指向外面也不许(roots, tmp_path):
    """软链接逃逸 —— 比较的是解析后的真实路径。"""
    link = roots / "escape"
    outside = tmp_path / "outside"
    outside.mkdir()
    link.symlink_to(outside)
    assert is_allowed(link) is False


def test_白名单里的仓库可以绑定(roots):
    service = EnvironmentService()
    binding = service.bind("t_env_1", roots / "demo")
    assert binding.kind == "folder"
    assert binding.branch == "main"
    assert service.current("t_env_1") is not None
    service.unbind("t_env_1")
    assert service.current("t_env_1") is None


# ---------- 分支名 ----------


def test_分支名非法要报错():
    with pytest.raises(ValidationError):
        validate_branch("bad..name")
    with pytest.raises(ValidationError):
        validate_branch("")
    assert validate_branch("feat/x-1") == "feat/x-1"


def test_slug_去掉危险字符():
    assert slugify("feat/登录 页面!") == "feat-登录-页面"
    assert slugify("") == "task"


# ---------- git ----------


@needs_git
def test_仓库状态与提交(roots):
    git = GitService(roots / "demo")
    status = git.status()
    assert status.is_repo is True
    assert status.branch == "main"
    assert status.dirty is False

    (roots / "demo" / "a.txt").write_text("新文件\n", encoding="utf-8")
    result = git.commit("加个文件")
    assert result["committed"] is True
    assert result["sha"]
    # 没有改动就不提交 —— 空提交没有意义
    assert git.commit("再提交一次")["committed"] is False


@needs_git
def test_worktree_建了列了删了(roots):
    service = EnvironmentService()
    env = service.create_worktree(roots / "demo", "feat/试验")
    assert env.kind == "worktree"
    assert env.branch == "feat/试验"
    assert Path(env.path).is_dir()
    # 分支真的建出来了
    assert "feat/试验" in GitService(roots / "demo").branch_list()
    # 清单里能看到
    paths = {item.path for item in GitService(roots / "demo").worktree_list()}
    assert env.path in paths

    service.remove_worktree(env.path)
    assert not Path(env.path).exists()


@needs_git
def test_只能删平台建的_worktree(roots):
    """用户自己 git worktree add 出来的目录我们不碰。"""
    service = EnvironmentService()
    with pytest.raises(ValidationError):
        service.remove_worktree(roots / "demo")


@needs_git
def test_合并分支(roots):
    repo = roots / "demo"
    git = GitService(repo)
    git.checkout("feat/另一条", "-b")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    git.commit("加 b")
    git.checkout("main")
    result = git.merge("feat/另一条")
    assert result["merged"] is True
    assert (repo / "b.txt").exists()


@needs_git
def test_同一个分支再建_worktree_会复用(roots):
    """重放一次调用不能多出一份 —— 幂等。"""
    service = EnvironmentService()
    first = service.create_worktree(roots / "demo", "feat/复用")
    second = service.create_worktree(roots / "demo", "feat/复用", reuse=True)
    assert first.path == second.path
    service.remove_worktree(first.path)


# ---------- 接口 ----------


async def test_环境清单接口(client, monkeypatch):
    """没配白名单时，接口要明说"没开这个功能"，而不是给一个空列表。"""
    monkeypatch.setattr(settings, "repo_roots", "")
    response = await client.get("/api/v1/environments")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False
    assert data["environments"] == []


async def test_绑白名单外的目录要被拒(client, roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    response = await client.post(
        "/api/v1/environments/binding/t_api_1",
        json={"path": str(outside)},
    )
    assert response.status_code == 422
    assert "范围" in response.json()["message"]


async def test_绑定与查看绑定(client, roots):
    response = await client.post(
        "/api/v1/environments/binding/t_api_2",
        json={"path": str(roots / "demo")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["branch"] == "main"

    response = await client.get("/api/v1/environments/binding/t_api_2")
    assert response.json()["data"]["path"] == str(roots / "demo")

    response = await client.delete("/api/v1/environments/binding/t_api_2")
    assert response.status_code == 200
    response = await client.get("/api/v1/environments/binding/t_api_2")
    assert response.json()["data"]["path"] == ""


@needs_git
async def test_建_worktree_的接口(client, roots):
    response = await client.post(
        "/api/v1/environments",
        json={"repo_path": str(roots / "demo"), "branch": "feat/接口建的"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["branch"] == "feat/接口建的"
    assert Path(data["path"]).is_dir()


@needs_git
async def test_不填分支名就自动生成(client, roots):
    """选中仓库就该直接能用 —— 不该逼着人先想好分支叫什么。"""
    response = await client.post(
        "/api/v1/environments",
        json={"repo_path": str(roots / "demo")},
    )
    assert response.status_code == 200
    branch = response.json()["data"]["branch"]
    assert branch.startswith("qzd/")
    assert "demo" in branch


async def test_绑定可以带名字(client, roots):
    """用户给环境起个名 —— 之后在界面上好认。"""
    response = await client.post(
        "/api/v1/environments/binding/t_api_3",
        json={"path": str(roots / "demo"), "label": "我的应用"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "我的应用"

    response = await client.get("/api/v1/environments/binding/t_api_3")
    assert response.json()["data"]["name"] == "我的应用"


# ---------- 目录浏览 ----------


async def test_浏览目录列出子目录(client, roots):
    """选文件夹靠它 —— 白名单下几十层目录，一个下拉列表挑不动。"""
    response = await client.get("/api/v1/environments/browse")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["path"] == str(roots)
    names = [entry["name"] for entry in data["entries"]]
    assert "demo" in names


async def test_浏览能往下钻也能往回退(client, roots):
    (roots / "demo" / "子目录").mkdir()
    response = await client.get(
        "/api/v1/environments/browse",
        params={"path": str(roots / "demo")},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["path"] == str(roots / "demo")
    assert data["parent"] == str(roots)
    assert [entry["name"] for entry in data["entries"]] == ["子目录"]


async def test_浏览白名单外的目录要被拒(client, roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    response = await client.get(
        "/api/v1/environments/browse",
        params={"path": str(outside)},
    )
    assert response.status_code == 422
