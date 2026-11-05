"""上下文服务：收集各来源的块 → 装配成提示词。

## 分工

| 谁 | 管什么 |
| --- | --- |
| 记忆模块 | **召回什么**（排序、预算装箱、写入门禁） |
| 上下文模块（本模块） | **注入什么、放在哪、装不下怎么办** |

记忆模块的 ``recall_catalog`` 逻辑一行不改 —— 它管"记忆这件事"，
这里管"提示词这件事"。

## 块从哪来

| 块 | 来源 | 优先级 | 装不下时 |
| --- | --- | --- | --- |
| 基准提示词 | `app/agents/prompts.py` | 0（必装） | 截断 |
| 运行环境 | 工作区路径说明 | 10 | 丢 |
| 策略说明 | 能力层（审批策略） | 20 | 丢（**会警告**） |
| 上传件 | 文件模块（`uploads/` 清单） | 25 | 丢 |
| 工作区说明 | AGENTS.md | 30 | 截断 |
| 反思记录 | agent 层（最近的失败） | 35 | 丢 |
| 记忆目录 | 记忆模块 | 40 | 丢 |

## 为什么反思记录比记忆目录更该保住

记忆目录是**背景资料** —— 丢了 agent 少知道点事，下次还能再召。
反思记录是**"你刚才失败过"** —— 丢了 agent 就会在同一个坑里再摔一次，
而且它自己不知道。

两者都短，但这条更贵：重复摔同一个坑要花真的 token 与时间。

## 为什么策略说明的优先级比工作区说明高

策略说明告诉模型"哪些操作要人批"。它丢了，模型会反复触发审批、反复中断
任务 —— 用户看到的是"这 agent 怎么一直要确认"。工作区说明丢了只是少了些
项目规矩的细节，严重程度低一档。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.context.assembler import AssembledContext, assemble
from app.context.blocks import (
    OVERFLOW_DROP,
    OVERFLOW_TRUNCATE,
    ContextBlock,
)
from app.context.budget import budget_from_settings

logger = logging.getLogger(__name__)

# 块的 key —— 排查与测试断言用
KEY_BASE_PROMPT = "base"
KEY_RUNTIME = "runtime"
KEY_POLICY = "policy"
KEY_WORKSPACE = "workspace"
KEY_MEMORY = "memory"
KEY_UPLOADS = "uploads"
KEY_REFLECTION = "reflection"

# 优先级
PRIORITY_BASE = 0
PRIORITY_RUNTIME = 10
PRIORITY_POLICY = 20
# 上传件比工作区说明更该保住 —— 它是**本轮的工作材料**，而 AGENTS.md
# 里的项目规矩晚一轮再读也来得及。
PRIORITY_UPLOADS = 25
PRIORITY_WORKSPACE = 30
# 反思记录比记忆目录更该保住 —— 详见模块文档。
PRIORITY_REFLECTION = 35
PRIORITY_MEMORY = 40

# 运行时说明的标题
RUNTIME_TITLE = "运行环境："


@dataclass
class ContextRequest:
    """一次上下文组装的输入。

    - ``base_prompt`` —— 基准提示词（必装）；
    - ``workspace`` —— 工作区路径，给模型解释路径该怎么用；
    - ``memory_catalog`` —— 记忆目录的正文（记忆模块给的）；
    - ``reflection_note`` —— 反思记录（agent 层给的：最近的失败）；
    - ``policy_note`` —— 策略说明（能力层给的）；
    - ``context_window`` —— 模型窗口（token），0 = 未知。
    """

    base_prompt: str = ""
    workspace: Path | None = None
    memory_catalog: str = ""
    reflection_note: str = ""
    policy_note: str = ""
    uploads_note: str = ""
    context_window: int = 0
    # 额外块（调用方自己加的）
    extra_blocks: list[ContextBlock] = field(default_factory=list)


@dataclass
class ContextResult:
    """组装结果 + 诊断信息。

    诊断信息给"预览接口"用 —— 拼装结果看不见就没法排查。
    """

    text: str = ""
    budget_chars: int = 0
    used_chars: int = 0
    blocks: list[ContextBlock] = field(default_factory=list)
    dropped: list[ContextBlock] = field(default_factory=list)
    truncated: list[ContextBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_assembled(cls, assembled: AssembledContext) -> ContextResult:
        return cls(
            text=assembled.text,
            budget_chars=assembled.budget_chars,
            used_chars=assembled.used_chars,
            blocks=assembled.blocks,
            dropped=assembled.dropped,
            truncated=assembled.truncated,
            warnings=assembled.warnings,
        )


class ContextService:
    """上下文装配服务。

    这一层没有状态 —— 全部输入都靠参数传进来。这样它在 agent 的恢复路径上
    （另一次 HTTP 请求、可能是另一个进程）也能安全地用。
    """

    def __init__(self, *, context_window: int = 0) -> None:
        """构造。

        Args:
            context_window: 模型窗口（token），0 = 未知。
        """
        self.context_window = context_window

    def collect(self, request: ContextRequest) -> list[ContextBlock]:
        """收集本次要装的块。

        各来源的块在这里汇合 —— 谁提供什么内容一目了然。
        """
        blocks: list[ContextBlock] = []

        if request.base_prompt.strip():
            blocks.append(
                ContextBlock(
                    key=KEY_BASE_PROMPT,
                    content=request.base_prompt,
                    priority=PRIORITY_BASE,
                    title="",
                    overflow=OVERFLOW_TRUNCATE,
                    required=True,
                )
            )

        runtime = self._runtime_block(request.workspace)
        if runtime:
            blocks.append(runtime)

        if request.policy_note.strip():
            blocks.append(
                ContextBlock(
                    key=KEY_POLICY,
                    content=request.policy_note,
                    priority=PRIORITY_POLICY,
                    title="",
                    overflow=OVERFLOW_DROP,
                    required=False,
                )
            )

        if request.memory_catalog.strip():
            blocks.append(
                ContextBlock(
                    key=KEY_MEMORY,
                    content=request.memory_catalog,
                    priority=PRIORITY_MEMORY,
                    # 记忆目录自带 XML 标签，不要再套一层标题 ——
                    # 标签本身就是给模型看的边界
                    title="",
                    overflow=OVERFLOW_DROP,
                )
            )

        if request.reflection_note.strip():
            blocks.append(
                ContextBlock(
                    key=KEY_REFLECTION,
                    content=request.reflection_note,
                    priority=PRIORITY_REFLECTION,
                    # 同样自带 XML 标签
                    title="",
                    overflow=OVERFLOW_DROP,
                )
            )

        if request.uploads_note.strip():
            blocks.append(
                ContextBlock(
                    key=KEY_UPLOADS,
                    content=request.uploads_note,
                    priority=PRIORITY_UPLOADS,
                    # 同样自带 XML 标签
                    title="",
                    overflow=OVERFLOW_DROP,
                )
            )

        blocks.extend(request.extra_blocks)
        return blocks

    def assemble(self, request: ContextRequest) -> ContextResult:
        """收集 + 装配，一次到位。

        Args:
            request: 组装输入。

        Returns:
            组装结果（含诊断信息）。
        """
        blocks = self.collect(request)
        budget = budget_from_settings(request.context_window or self.context_window)
        assembled = assemble(blocks, budget_chars=budget)
        result = ContextResult.from_assembled(assembled)
        for warning in assembled.warnings:
            logger.warning("上下文装配：%s", warning)
        return result

    def _runtime_block(self, workspace: Path | None) -> ContextBlock | None:
        """运行时说明块。

        **内容只有一条**：路径怎么用。

        之前这条写的是"宿主路径只在拼命令时用" —— **这是错的**。沙箱（docker
        模式）里只挂载了工作区，宿主机上的绝对路径在容器里根本不存在，模型
        照着拼一条 ``cd /Users/...`` 只会换来 "can't cd to"，白烧一轮。
        现在只说"用工作区里的相对路径"，两种模式都对。
        """
        if workspace is None:
            return None
        note = (
            "文件工具的路径是虚拟路径，以 `/` 为工作区根目录；"
            "命令的工作目录也是工作区根，用工作区里的相对路径。"
            "不要用宿主机的绝对路径 —— 沙箱里没有那条路径，cd 一定失败。"
        )
        return ContextBlock(
            key=KEY_RUNTIME,
            content=note,
            priority=PRIORITY_RUNTIME,
            title=RUNTIME_TITLE,
            overflow=OVERFLOW_DROP,
        )


def build_context_service(context_window: int = 0) -> ContextService:
    """构造上下文服务。

    Args:
        context_window: 模型窗口（token），0 = 未知。

    Returns:
        上下文服务。
    """
    return ContextService(context_window=context_window)


__all__ = [
    "KEY_BASE_PROMPT",
    "KEY_MEMORY",
    "KEY_POLICY",
    "KEY_REFLECTION",
    "KEY_RUNTIME",
    "KEY_UPLOADS",
    "KEY_WORKSPACE",
    "PRIORITY_BASE",
    "PRIORITY_MEMORY",
    "PRIORITY_POLICY",
    "PRIORITY_REFLECTION",
    "PRIORITY_RUNTIME",
    "PRIORITY_UPLOADS",
    "PRIORITY_WORKSPACE",
    "ContextRequest",
    "ContextResult",
    "ContextService",
    "build_context_service",
]
