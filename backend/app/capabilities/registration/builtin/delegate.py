"""委派能力：派一个子 agent（worker）去干活。

这是「多智能体协作」在平台上的入口。主 agent 判断任务能拆开时，调这个能力
把其中一块交给 worker —— worker 在**自己的 worktree** 里干完提交，主 agent
拿到分支与结论。

**模型自己决定要不要拆、拆成几块** —— 平台不提供"并行派 N 个"的控件，
拆任务本来就是模型擅长的部分。

worker 的三个特点（详见 :mod:`app.agents.worker`）：

- 独立 worktree：和主 agent 互不干扰，可以真并行；
- **不挂人工审批**：worker 没有界面，挂起就等不到人批。防线是物理隔离 ——
  自己的 worktree + 断网容器；
- 递归封顶：worker 的能力清单里没有这个能力，深度最多两层。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agents.worker import run_worker
from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.context import ExecutionContext
from app.environments.naming import slugify
from app.git.service import GitService

logger = logging.getLogger(__name__)


def _default_branch(task: str) -> str:
    """给 worker 起一个默认分支名。

    带 ``qzd/`` 前缀 —— 一眼看出是平台派出去的分支，不会跟人自己的分支混。
    """
    return f"qzd/{slugify(task)[:32]}-{uuid4().hex[:4]}"


def make_delegate_worker(
    context: ExecutionContext,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """构造"派子 agent 干活"能力。"""

    async def delegate_worker(
        task: str,
        branch: str | None = None,
        base: str | None = None,
    ) -> dict[str, Any]:
        """把一个子任务交给子 agent，在独立分支上完成。

        适合这些情况：
        - 一堆互不相干的活（改 A 模块、同时改 B 模块）—— 可以并行；
        - 调研类的活（把某个目录读一遍出个结论）—— 不占主对话的上下文；
        - 改动大到自己容易改乱 —— 隔开一份更稳。

        子 agent 会在**新的 worktree**（独立检出 + 独立分支）里干活，
        做完提交在那个分支上。分支不会自动合回主线 —— 你可以先看，
        确认没问题再用 git_merge 合回去。

        Args:
            task: 子任务描述。要**自包含** —— 子 agent 看不到你这里的对话。
            branch: 子 agent 的分支名；不给就自动起一个。
            base: 基点分支或提交；不给就是当前 HEAD。

        Returns:
            ``content`` 是给主 agent 看的总结；另有 ``branch`` / ``worktree`` /
            ``sha`` 等结构化字段。
        """
        text = (task or "").strip()
        if not text:
            raise CapabilityError("子任务描述不能为空", code=ErrorCode.INVALID_ARGUMENTS)

        git = GitService(context.workspace)
        root = git.repo_root()
        if root is None:
            raise CapabilityError(
                "当前工作目录不是 git 仓库，派不了子 agent（子 agent 需要在分支上干活）",
                code=ErrorCode.INVALID_ARGUMENTS,
            )

        model = context.model or _fallback_model()
        specs = list(context.specs or [])
        branch_name = (branch or "").strip() or _default_branch(text)

        result = await run_worker(
            task=text,
            repo_root=Path(root),
            branch=branch_name,
            base=base,
            specs=specs,
            model=model,
            parent_thread_id=context.thread_id,
            # **共用父会话的数据库会话与锁** —— worker 的调用记录要写进同一个库，
            # 而同一个 AsyncSession 不能并发使用：worker 与外层那些并行的工具
            # 调用必须共用一把锁才不打架。
            session=context.session,
            db_lock=context.db_lock,
        )

        lines = [
            f"子 agent 完成了子任务（分支 {result.branch}）。",
            f"工作目录：{result.worktree}",
        ]
        if result.committed:
            lines.append(f"已提交：{result.sha}")
            if result.files:
                lines.append("改动的文件：")
                lines.extend(f"  - {item}" for item in result.files[:20])
        else:
            lines.append("没有产生改动（工作区是干净的）。")
        if result.error:
            lines.append(f"注意：{result.error}")
        lines.append("子 agent 的汇报：")
        lines.append(result.content or "（没有给出文字总结）")
        lines.append(
            f"提示：这个分支还没合回主线。要合的话用 git_merge({result.branch})，"
            "或者直接告诉用户分支名，由人来合并。"
        )
        return {
            "content": "\n".join(lines),
            "branch": result.branch,
            "worktree": result.worktree,
            "committed": result.committed,
            "sha": result.sha,
            "files": result.files,
            "summary": result.content,
        }

    return delegate_worker


def _fallback_model() -> Any:
    """没有从上下文拿到模型时，用环境变量里的配置兜底。

    正常路径是上下文里带着**本次请求用的那个模型**（用户在界面上选的
    供应商），所以这个兜底只在测试或非常规调用里才会走到。
    """
    from app.agents.llm import get_chat_model

    return get_chat_model()


__all__ = ["make_delegate_worker"]
