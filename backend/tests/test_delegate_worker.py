"""派子 agent（worker）的测试。

验证三件事：

1. worker 真的在**自己的 worktree** 里干活（改的文件落在那个目录里）；
2. 干完的活**提交在它自己的分支**上；
3. worker 的状态**不会写进主会话的 checkpoint** —— 那是"父图被冲掉"那类
   静默损坏的来源。
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.builtin.delegate import make_delegate_worker
from app.core.config import settings
from app.git.service import GitService

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git 才能测")


class WritingModel(BaseChatModel):
    """先调一次 ``write_file``，然后回一句话。

    用来验证 worker 真的在自己的目录里写了东西 —— 只回文本的话，
    什么也证明不了。
    """

    reply: str = "写完了。"

    @property
    def _llm_type(self) -> str:
        return "writing"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "WritingModel":
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        already_called = any(
            isinstance(msg, AIMessage) and msg.tool_calls for msg in messages
        )
        if already_called:
            message = AIMessage(content=self.reply)
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/note.md", "content": "worker 写的\n"},
                        "id": "call_1",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _init_repo(path: Path) -> Path:
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
def repo(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    _init_repo(root / "demo")
    monkeypatch.setattr(settings, "repo_roots", str(root))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    # worktree 的落点也要挪进临时目录 —— 否则测试会动到真实的 worktree 根
    monkeypatch.setattr(settings, "worktree_root", str(tmp_path / "worktrees"))
    return root / "demo"


@needs_git
async def test_worker_在自己的分支上干活并提交(repo):
    context = ExecutionContext.build(repo)
    context.model = WritingModel()
    context.specs = []

    delegate = make_delegate_worker(context)
    result = await delegate(task="在 note.md 里写一行字", branch="qzd/测试")

    assert result["branch"] == "qzd/测试"
    worktree = Path(result["worktree"])
    assert worktree.is_dir()
    # 文件写在 worktree 里，不是主仓库里
    assert (worktree / "note.md").read_text(encoding="utf-8") == "worker 写的\n"
    assert not (repo / "note.md").exists()
    # 提交落在它自己的分支上
    assert result["committed"] is True
    assert result["sha"]
    assert "qzd/测试" in GitService(repo).branch_list()
    assert GitService(worktree).status().branch == "qzd/测试"


@needs_git
async def test_不给分支名就自动起一个(repo):
    context = ExecutionContext.build(repo)
    context.model = WritingModel()
    context.specs = []

    delegate = make_delegate_worker(context)
    result = await delegate(task="随便做点什么")

    assert result["branch"].startswith("qzd/")
    assert result["worktree"]


@needs_git
async def test_空任务要报错(repo):
    context = ExecutionContext.build(repo)
    context.model = WritingModel()
    delegate = make_delegate_worker(context)
    with pytest.raises(Exception):
        await delegate(task="   ")


async def test_不是仓库就派不了(tmp_path):
    """没有 git 仓库就没法开分支 —— 要说清楚，别让模型瞎试。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    context = ExecutionContext.build(plain)
    context.model = WritingModel()
    delegate = make_delegate_worker(context)
    with pytest.raises(Exception) as excinfo:
        await delegate(task="干活")
    assert "git" in str(excinfo.value)
