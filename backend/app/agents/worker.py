"""子 agent（worker）：在**独立的 worktree** 上跑一个子任务。

## 为什么自己跑，而不是用 deepAgents 的 ``task`` 工具

deepAgents 自带的 ``task`` 工具派的子代理**和主 agent 共用同一个工作区** ——
两个子代理同时改同一个目录，就是互相覆盖。

这里派 worker 时给它**自己的 worktree**（自己的一份检出 + 自己的分支）：

```text
主 agent（工作区 A）
   │  delegate_worker(任务)
   ▼
worker（worktree B，分支 qzd/xxx）  ← 独立目录、独立分支、独立沙箱容器
   │  做完提交
   ▼
主 agent 拿到分支与提交 → 用户手动合回主线
```

## worker 与主 agent 的三处不同

| | 主 agent | worker |
| --- | --- | --- |
| 工作目录 | 会话绑定的环境 | 自己的 worktree |
| 人工审批 | 有（前端点批准） | **无**（没有界面，挂起就等不到人批） |
| 历史 | 有 checkpointer，多轮 | 无，一次性执行 |

**审批那一条是安全上的关键取舍**：worker 里的写操作与命令执行不需要人批。
唯一的防线是物理隔离 —— 它只在自己的 worktree 里干活，命令在断网的 docker
容器里跑、只挂载那个 worktree。这条取舍是用户明确选的。

**递归到此为止**：worker 的能力清单里**没有** ``delegate_worker`` 本身，
所以它派出去的子代理也无法再派 —— 深度封顶在两层。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.builder import build_agent
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.spec import CapabilitySpec
from app.environments.service import EnvironmentService
from app.git.service import GitService

logger = logging.getLogger(__name__)

# worker 的系统提示词。
#
# 短是故意的：worker 干的是一件具体的活，不需要主 agent 那套工作准则。
# 但有三条必须写清楚 —— 改错地方、别管别的分支、干完要提交。
WORKER_PROMPT = """你是一个子 agent，主 agent 把一段明确的子任务交给你。

你在**一个独立的 git worktree** 里工作，这个目录就是你的全部世界：
- 只在这个目录里改文件。不要去改别的目录，也不要试图操作主仓库；
- 你看到的分支就是你的分支，提交留在你的分支上，由人决定要不要合回去；
- 遇到要做判断的事（范围不清、需求有歧义），按最合理的理解做，并在最后
  的回复里说清你的理解和取舍。

关于 git：
- **命令沙箱里没有 git，也没有仓库的 .git 目录** —— 沙箱只挂载你这个
  worktree，里面的 .git 是个指向主仓库的指针文件，容器里解析不了；
- 所以**不要在 execute 里敲 git 命令**，那一定失败；
- 要看状态用 git_status，要提交用 git_commit —— 平台在宿主机上代做。

工作准则：
1. 先读现有代码再动手，不要凭空猜文件内容；
2. 改动聚焦在交给你的这件事上，不顺手做无关的重构；
3. 做完自己验证（能跑就跑一遍），最后用中文总结：
   改了什么、动了哪些文件、有什么风险或没做完的地方。
"""

# worker 不许再派活：递归到此为止
NO_DELEGATE_KEY = "delegate_worker"


@dataclass
class WorkerResult:
    """一个 worker 跑完的结果。"""

    worker_id: str
    branch: str
    worktree: str
    content: str = ""
    committed: bool = False
    sha: str = ""
    files: list[str] = field(default_factory=list)
    steps: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "branch": self.branch,
            "worktree": self.worktree,
            "content": self.content,
            "committed": self.committed,
            "sha": self.sha,
            "files": self.files,
            "steps": self.steps,
            "error": self.error,
        }


async def run_worker(
    *,
    task: str,
    repo_root: Path,
    branch: str,
    base: str | None = None,
    specs: list[CapabilitySpec],
    model: Any,
    parent_thread_id: str = "",
    session: Any = None,
    db_lock: Any = None,
    max_steps: int = 40,
) -> WorkerResult:
    """派一个 worker 去干活。

    Args:
        task: 交给 worker 的子任务描述（要自包含 —— worker 看不到主对话）。
        repo_root: 仓库根目录（从它派生 worktree）。
        branch: worker 的分支名。
        base: 基点分支/提交；不给就是仓库当前 HEAD。
        specs: 本次可用的能力清单（会去掉 ``delegate_worker``）。
        model: 用的模型对象（跟主 agent 同一个）。
        parent_thread_id: 主会话 ID（记在结果里，方便回溯）。
        session: 数据库会话（worker 的调用记录写进去）。
        db_lock: 数据库锁。**必须跟父会话共用一把** —— 同一个 AsyncSession
            不能并发使用，worker 与外层那些并行的工具调用会撞在一起。
        max_steps: worker 最多走多少步。

    Returns:
        worker 的结果（分支、提交、总结）。
    """
    # **worker 的标识要短**：沙箱容器名按它派生，而容器名会被截断到 48 字符 ——
    # 带上主会话 id 就可能撞名（两个 worker 抢同一个容器，来回重建、依赖全丢）。
    worker_id = f"w{uuid4().hex[:8]}"
    service = EnvironmentService()
    env = service.create_worktree(repo_root, branch, base=base, reuse=True)

    # worker 的能力清单：去掉委派本身 —— 递归到此为止
    worker_specs = [spec for spec in specs if spec.key != NO_DELEGATE_KEY]

    context = ExecutionContext.build(env.path)
    context.session = session
    # **独立的执行标识**：worker 的工具调用要走自己的并发名额 ——
    # 用主会话的标识会和外层那次 delegate_worker 调用抢同一个名额，
    # 并行派多个 worker 时会把自己堵死。
    context.thread_id = worker_id
    if db_lock is not None:
        context.db_lock = db_lock

    git = GitService(env.path)
    # 跑之前的 HEAD —— 跑完之后如果它变了，说明 worker 自己提交过
    head_before = git.head_sha()

    agent = await build_agent(
        specs=worker_specs,
        context=context,
        checkpointer=None,  # 一次性执行，不需要多轮历史
        model=model,
        system_prompt=WORKER_PROMPT,
        thread_id=worker_id,
        trust_mode="permissive",
        allow_interrupt=False,  # 没有界面，挂起就等不到人批
        skills=None,
        skills_dir=None,
        memory=None,
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        # **显式给 thread_id**：不传的话会继承外层图的 ambient config
        # （连同父图的 checkpointer），worker 的状态会写进主会话的 checkpoint
        # 谱系里，把主会话冲掉 —— 而且不报错。
        #
        # recursion_limit 也要显式给：默认 25，worker 走二十几步工具
        # 就会 GraphRecursionError。
        config={"configurable": {"thread_id": worker_id}, "recursion_limit": max_steps + 20},
    )

    messages = result.get("messages", []) if isinstance(result, dict) else []
    content = _final_text(messages)
    steps = sum(1 for msg in messages if isinstance(msg, AIMessage) and msg.tool_calls)

    committed = False
    sha = ""
    files: list[str] = []
    error = ""
    try:
        status = git.status()
        if status.dirty:
            # 还有没提交的改动 —— 平台代它提交（worker 自己没提交的情况）
            title = task.strip().splitlines()[0][:60] if task.strip() else "子任务"
            commit = git.commit(f"worker: {title}")
            committed = bool(commit["committed"])
            sha = str(commit["sha"])
            files = list(commit["files"])
        else:
            # 工作区是干净的 —— worker 自己提交过了（它有 git_commit 能力）
            sha = git.head_sha()
            committed = sha != head_before
    except Exception as exc:  # pragma: no cover - 提交失败不该让整个派活失败
        error = f"提交失败：{exc}"
        logger.warning("worker 提交失败 %s: %s", worker_id, exc)

    return WorkerResult(
        worker_id=worker_id,
        branch=env.branch,
        worktree=env.path,
        content=content,
        committed=committed,
        sha=sha,
        files=files,
        steps=steps,
        error=error,
    )


def _final_text(messages: list[Any]) -> str:
    """取 worker 最后一条回复的正文。"""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = msg.content
            if isinstance(text, str) and text.strip():
                return text
            if isinstance(text, list):
                parts = [
                    block.get("text", "")
                    for block in text
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                joined = "\n".join(part for part in parts if isinstance(part, str))
                if joined.strip():
                    return joined
    return ""


__all__ = ["NO_DELEGATE_KEY", "WORKER_PROMPT", "WorkerResult", "run_worker"]
