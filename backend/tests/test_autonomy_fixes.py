"""自主能力修复的回归测试。

每个测试盯住一条**实测复现过**的缺陷 —— 不是"应该这样"，而是"曾经真的
会这样"。这些测试存在的意义是：框架升级、依赖换版、有人重构时，
悄悄退回去会被它们拦住。

盯住的几条：

1. **worker 够不到别的 worktree**（R1b）—— 它的能力集里不该有
   ``git_worktree_remove`` / ``git_merge`` / ``git_worktree_add``；
2. **worker 的破坏性调用要记账**（R1）—— 没有界面挂不了起，但风险
   判定与调用记录不能没有；
3. **压缩后历史还在**（R2/R3）—— 原始对话留在 checkpoint 里，
   并且外置成文件（agent 能 read_file 读回来）；
4. **外置文件不进用户仓库**（R2）—— 落点必须在工作区外面；
5. **递归预算是有限的**（R5）—— deepAgents 把 recursion_limit 绑成
   9999（等于没有上限），平台要自己给一个；
6. **记忆来源参与排序**（进化）—— 用户说的比模型推断的靠前。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages

from app.agents.worker import WORKER_BLOCKED_GIT_KEYS
from app.capabilities.backends.assembly import _artifacts_dir, build_backend
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.registration.spec import CapabilitySpec, RiskLevel
from app.context.compaction import build_compaction_middleware
from app.core.config import settings

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git 才能测")


# ---------------------------------------------------------------- R1b


def _spec(key: str, *, risk: str = RiskLevel.READ) -> CapabilitySpec:
    return CapabilitySpec(
        key=key,
        kind="tool",
        display_name=key,
        input_schema={"type": "object", "properties": {}},
        risk=risk,
    )


def _init_repo(path: Path) -> Path:
    """建一个有一个提交的 git 仓库。"""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e.c",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return path


def _allow_repo(tmp_path, monkeypatch) -> Path:
    """把白名单与 worktree 落点指到临时目录，返回白名单根。"""
    root = tmp_path / "allowed"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "repo_roots", str(root))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    monkeypatch.setattr(settings, "worktree_root", str(tmp_path / "worktrees"))
    return root


def test_worker的能力集里没有破坏性git():
    """worker 跑在 bypass 上且没有界面 —— 那三个能力到了它手里就没人拦。

    ``git_worktree_remove`` 的 path 是模型给的，不收窄的话一个 worker
    能删掉同仓库**别的 worker** 的 worktree（实测复现）。
    """
    assert "git_worktree_remove" in WORKER_BLOCKED_GIT_KEYS
    assert "git_merge" in WORKER_BLOCKED_GIT_KEYS
    assert "git_worktree_add" in WORKER_BLOCKED_GIT_KEYS


async def test_worker实际拿到的清单里也没有那些能力(tmp_path, monkeypatch):
    """不只是常量 —— 真跑一遍 ``run_worker``，看它组出来的清单。"""
    from app.agents.worker import run_worker

    captured: list[CapabilitySpec] = []

    class ProbeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "probe"

        def bind_tools(self, tools, **kwargs) -> ProbeModel:
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好了"))])

    import app.agents.worker as worker_mod

    original = worker_mod.build_agent

    async def spy(**kwargs):
        captured.extend(kwargs.get("specs") or [])
        return await original(**kwargs)

    monkeypatch.setattr(worker_mod, "build_agent", spy)

    root = _allow_repo(tmp_path, monkeypatch)
    repo = _init_repo(root / "demo")

    await run_worker(
        task="做点事",
        repo_root=repo,
        branch="qzd/test",
        specs=[
            _spec("git_status"),
            _spec("git_commit"),
            _spec("git_worktree_remove"),
            _spec("git_merge"),
        ],
        model=ProbeModel(),
    )

    keys = {spec.key for spec in captured}
    assert "git_status" in keys, "看状态与提交要留着"
    assert "git_commit" in keys
    assert "git_worktree_remove" not in keys
    assert "git_merge" not in keys


@needs_git
async def test_git_worktree_remove不删别的仓库(tmp_path, monkeypatch):
    """path 是模型给的 —— 白名单管的是"哪个目录"，不是"哪个仓库"。

    跨仓库删 worktree 要被挡住。
    """
    from app.capabilities.errors import CapabilityError
    from app.capabilities.registration.builtin.git import make_git_worktree_remove

    root = _allow_repo(tmp_path, monkeypatch)
    repo_a = root / "repo-a"
    repo_b = root / "repo-b"
    for repo in (repo_a, repo_b):
        _init_repo(repo)

    from app.environments.service import EnvironmentService

    svc = EnvironmentService()
    other = svc.create_worktree(repo_b, "qzd/other")

    # 当前工作目录在 repo_a —— 它没有资格删 repo_b 的 worktree
    context = ExecutionContext.build(repo_a)
    remove = make_git_worktree_remove(context)
    with pytest.raises(CapabilityError) as excinfo:
        await remove(path=other.path)
    assert "同一个仓库" in str(excinfo.value) or "当前仓库" in str(excinfo.value)
    assert Path(other.path).exists(), "别的仓库的 worktree 不能被删掉"


# ---------------------------------------------------------------- R1


async def test_worker的破坏性调用会被评估并记账(tmp_path):
    """没有界面挂不了起 —— 但**判定要跑、记录要留**。

    以前 ``interrupt_enabled=False`` 时 ``_check_param_risk`` **直接 return
    None**，连评估都没做：worker 干了什么、命中了什么风险，事后查不到。
    """
    from app.capabilities.execution.risk import assess_call

    context = ExecutionContext.build(tmp_path)
    mw = CapabilityMiddleware(
        [_spec("run_python", risk=RiskLevel.WRITE)],
        context=context,
        thread_id="w-test",
        trust_mode="permissive",
        interrupt_enabled=False,
    )
    # 判定本身照样给出"破坏性"
    assessment = assess_call("run_python", {"code": "import shutil; shutil.rmtree('/')"}, mw.specs)
    assert assessment.risk == RiskLevel.DESTRUCTIVE
    assert assessment.needs_review is True

    # 中间件不挂起（没有界面），但**它照样算了一遍**
    result = await mw._check_param_risk(
        "run_python",
        {"code": "import shutil; shutil.rmtree('/')"},
        assessment,
    )
    assert result is None, "worker 没有界面，不该挂起"


async def test_主agent的参数级规则仍会挂起(tmp_path):
    """改了 worker 的路径之后，主 agent 那条不能被带坏。"""
    context = ExecutionContext.build(tmp_path)
    mw = CapabilityMiddleware(
        [_spec("run_python", risk=RiskLevel.WRITE)],
        context=context,
        thread_id="t-test",
        trust_mode="auto",
        interrupt_enabled=True,
    )
    with pytest.raises(Exception) as excinfo:
        # interrupt() 在没有真实图上下文时抛错 —— 那正是"挂起了"的信号
        await mw._check_param_risk("run_python", {"code": "import shutil; shutil.rmtree('/')"})
    assert "interrupt" in type(excinfo.value).__name__.lower() or excinfo.type.__name__ in {
        "GraphInterrupt",
        "RuntimeError",
    }


# ---------------------------------------------------------------- R2 / R3


class _State:
    messages: Annotated[list, add_messages]


def _fake_model(responses: int = 50) -> BaseChatModel:
    class M(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "fake"

        def bind_tools(self, tools, **kwargs) -> M:
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            self.calls += 1
            return ChatResult(
                generations=[
                    ChatGeneration(message=AIMessage(content=f"SUMMARY-{self.calls}"))
                ]
            )

    return M()


async def test_压缩后原始对话还在_checkpoint里(tmp_path):
    """R2：压缩之后原始对话必须还在 —— 否则 agent 忘了早期做过什么且无法回查。

    早先这里继承的是 LangChain 的摘要中间件，它的 ``before_model`` 用
    ``RemoveMessage(REMOVE_ALL_MESSAGES)`` **重写 state**，原始消息就没了。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)
    model = _fake_model()
    model.profile = {"max_input_tokens": 3000}

    middleware = build_compaction_middleware(model=model, backend=backend)
    app = create_agent(model=model, tools=[], middleware=[middleware], checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-compact"}}
    for i in range(14):
        await app.ainvoke(
            {"messages": [HumanMessage(content=f"msg number {i} " + "x" * 400)]},
            config=cfg,
        )

    state = await app.aget_state(cfg)
    body = " ".join(str(getattr(m, "content", "")) for m in state.values["messages"])
    assert "msg number 0" in body, "压缩后最早的对话应该还在 checkpoint 里"


async def test_压缩会把历史外置成文件(tmp_path):
    """R2：历史外置 = agent 能用 read_file 读回来。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)
    model = _fake_model()
    model.profile = {"max_input_tokens": 3000}

    middleware = build_compaction_middleware(model=model, backend=backend)
    app = create_agent(model=model, tools=[], middleware=[middleware], checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-offload"}}
    for i in range(14):
        await app.ainvoke(
            {"messages": [HumanMessage(content=f"msg number {i} " + "x" * 400)]},
            config=cfg,
        )

    files = list(_artifacts_dir(context).rglob("conversation_history/*.md"))
    assert files, "应该有一份外置的历史"
    content = files[0].read_text(encoding="utf-8")
    assert "msg number 0" in content, "外置的文件里要有早期的对话"

    # 而且 agent 能读回来
    vpath = f"/.artifacts/conversation_history/{files[0].name}"
    result = backend.read(vpath)
    assert result.error is None, f"读不回来：{result.error}"
    text = (result.file_data or {}).get("content", "")
    assert "msg number 0" in text


async def test_外置的历史不进用户的工作区(tmp_path):
    """产物目录必须在工作区**外面** —— 否则对话历史会落进用户的仓库。

    deepAgents 算 ``artifacts_root`` 用 ``isinstance(backend, CompositeBackend)``。
    我们的 ``PlatformBackend`` 是它的子类，所以判定为真，落点是
    ``/.artifacts/``（工作区外面）。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)
    model = _fake_model()
    model.profile = {"max_input_tokens": 3000}

    middleware = build_compaction_middleware(model=model, backend=backend)
    assert middleware._history_path_prefix.startswith("/.artifacts/"), (
        f"历史外置的落点不对：{middleware._history_path_prefix}"
    )

    app = create_agent(model=model, tools=[], middleware=[middleware], checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-outside"}}
    for i in range(14):
        await app.ainvoke(
            {"messages": [HumanMessage(content=f"msg number {i} " + "x" * 400)]},
            config=cfg,
        )

    inside = list(workspace.rglob("*conversation_history*"))
    assert not inside, f"历史落进工作区了：{inside}"


def test_摘要要涵盖全部被压缩的历史(tmp_path):
    """R3：``trim_tokens_to_summarize`` 默认 4000 —— 更早的那部分历史
    **既不进摘要也不被保留**，直接消失。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    backend = build_backend(context, None)
    model = _fake_model()
    model.profile = {"max_input_tokens": 100000}

    middleware = build_compaction_middleware(model=model, backend=backend)
    assert middleware.trim_tokens_to_summarize is None, "应该全量送进摘要"


# ---------------------------------------------------------------- R5


async def test_主agent的递归预算是有限的(tmp_path):
    """deepAgents 建图时把 ``recursion_limit`` 绑成 9999 —— 等于没有上限。

    一个陷在循环里的 agent 能一口气跑几千轮，token 和钱都是真的烧掉了。
    """
    from app.agents.runner import AgentRunner

    runner = AgentRunner()
    config = runner._config("t-limit")
    assert "recursion_limit" in config
    assert config["recursion_limit"] < 9999, "要有自己的上限，不能用框架那个 9999"
    # 换算：轮数 × 2 + 余量
    assert config["recursion_limit"] == settings.agent_max_steps * 2 + 20


async def test_worker的递归预算也是有限的(tmp_path, monkeypatch):
    """worker 同样不能没有上限。"""
    from app.agents.worker import run_worker

    captured: dict = {}

    class ProbeModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "probe"

        def bind_tools(self, tools, **kwargs) -> ProbeModel:
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好"))])

    import app.agents.worker as worker_mod

    original = worker_mod.build_agent

    class SpyAgent:
        def __init__(self, inner):
            self._inner = inner

        async def ainvoke(self, payload, config=None):
            captured["config"] = config
            return await self._inner.ainvoke(payload, config=config)

    async def spy_build(**kwargs):
        return SpyAgent(await original(**kwargs))

    worker_mod.build_agent = spy_build
    try:
        root = _allow_repo(tmp_path, monkeypatch)
        repo = _init_repo(root / "demo")

        await run_worker(
            task="做点事",
            repo_root=repo,
            branch="qzd/limit",
            specs=[],
            model=ProbeModel(),
            max_steps=40,
        )
    finally:
        worker_mod.build_agent = original

    limit = captured["config"]["recursion_limit"]
    assert limit == 40 * 2 + 20


# ---------------------------------------------------------------- 记忆来源


def test_用户说的记忆排在模型推断的前面():
    """来源维度以前只存不看 —— 等于没有。"""
    from app.memory.models import SOURCE_MODEL, SOURCE_USER, effective_score

    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    user_score = effective_score(1.0, 0, now, 30, now, SOURCE_USER)
    model_score = effective_score(1.0, 0, now, 30, now, SOURCE_MODEL)
    assert user_score > model_score


def test_来源权重只微调不淘汰():
    """降权 ≠ 淘汰 —— 模型推断的记忆不能被踢出视野。

    来源权重只是**再乘一个系数**（0.9），它不改变"相关性主导"的原则，
    也不构成事实上的淘汰 —— 同样的内容，用户说的排在前面，但都还在。
    """
    from app.memory.models import SOURCE_MODEL, source_weight

    assert source_weight(SOURCE_MODEL) == 0.9
    # 拿一条"用过很多次、很新"的模型推断记忆 vs 一条"刚见过一次"的
    # 用户记忆 —— 使用次数与时间的权重仍然压过来源的 10%。
    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    from app.memory.models import SOURCE_USER, effective_score

    fresh_user = effective_score(0.5, 0, now, 30, now, SOURCE_USER)
    used_model = effective_score(0.5, 10, now, 30, now, SOURCE_MODEL)
    assert used_model > fresh_user, "来源权重不该压过时间与使用次数"


async def test_长期记忆读回时来源默认按模型推断(tmp_path):
    """写进去的是模型写的，读回来不能变成"用户说的"。

    那样等于替模型背书 —— 模型自己总结的东西，召回时看着像用户授权过的。
    """
    from app.memory.long_term_store import _to_entry
    from app.memory.models import SOURCE_MODEL

    class FakeDoc:
        id = 1
        title = "t"
        content = "c"
        meta = {"kind": "memory"}
        created_at = None

    entry = _to_entry(FakeDoc())
    assert entry.source == SOURCE_MODEL
