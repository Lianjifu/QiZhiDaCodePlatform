"""Agent 组装：能力清单 + 执行环境 → 一个可执行的 deep agent。

用 deepAgents 的 ``create_deep_agent``，而不是 langchain 的 ``create_agent``。
deepAgents 额外带来规划（write_todos）、子代理（task）、技能（SKILL.md）、
记忆（AGENTS.md）、上下文摘要与 prompt caching。

**工具从哪来**：

- **文件工具**（ls / read_file / write_file / edit_file / delete / glob / grep /
  execute）：deepAgents 的 ``FilesystemMiddleware`` 注册，backend 换成我们的 ——
  文件操作走本地、命令执行走 docker 沙箱；
- **我们的工具**（web_fetch / web_search / run_python / MCP 工具）：从注册层来，
  走能力层的执行流水线。

同名工具不能同时存在：deepAgents 的文件工具名与我们的旧文件能力重名，
所以我们的文件类能力不再暴露成工具（它们的能力由 deepAgents 的文件工具接管）。

## 组装的顺序为什么是这样

```text
1. context.approval_handled = True   ← 必须最先
2. build_tools(...)                   ← 工具会读这个标记，决定要不要自己再问一次审批
3. 能力名查重
4. CapabilityMiddleware + TodoListMiddleware
5. _interrupt_on(...)                 ← 声明级审批闸，规则来自同一份 policy
6. _general_subagent(...)             ← 子代理显式挂同一套治理
7. create_deep_agent(...)
```

第 1 步的顺序是硬要求：**审批已经由 deepAgents 的 HITL 负责**（``interrupt_on``），
工具能跑到执行层就说明人已经批过了。执行层如果再问一遍，就出现"批准了等于
没批准"。这个标记必须在建工具**之前**设上，因为工具构造时就把"要不要自己
审批"固化进参数里了。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from app.capabilities.backends.assembly import build_backend
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.tooling import build_tools
from app.capabilities.intent.middleware import CapabilityMiddleware
from app.capabilities.registration.spec import CapabilitySpec
from app.capabilities.registration.tool_policy import FS_TOOL_NAMES, approval_required_tools
from app.context.compaction import build_compaction_middleware
from app.context.skills_quota import QuotaSkillsMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)

# 通用子代理的名字与说明。模型按 description 决定什么时候派活给它。
GENERAL_SUBAGENT_NAME = "general"
GENERAL_SUBAGENT_DESCRIPTION = (
    "通用子代理：适合需要独立上下文的复杂子任务（多文件调研、批量改造、"
    "长链路的排查）。它有自己的工具预算和上下文，不与主 agent 共享历史。"
)
GENERAL_SUBAGENT_PROMPT = (
    "你是通用子代理，主 agent 把一段完整、明确的子任务交给你。\n"
    "你的产出是给主 agent 用的中间结论，不是给人看的最终答复：\n"
    "- 先把任务做完，再给结论；\n"
    "- 结论要具体（文件路径、命令、数据），不要写“应该可以”之类的模糊话；\n"
    "- 遇到需要人工审批的操作，照常挂起等人批，不要绕过。"
)


async def build_agent(
    *,
    specs: list[CapabilitySpec],
    context: ExecutionContext,
    checkpointer: Any = None,
    model: BaseChatModel | None = None,
    system_prompt: str | None = None,
    allowed_tool_keys: set[str] | None = None,
    thread_id: str = "",
    trust_mode: str = "balanced",
    skills: list[str] | None = None,
    skills_dir: Path | None = None,
    memory: list[str] | None = None,
    subagents: list[Any] | None = None,
    allow_interrupt: bool = True,
) -> Any:
    """组装 deep agent。

    Args:
        specs: 注册层给出的统一能力清单。
        context: 执行上下文（工作区、沙箱配置、数据库会话等）。
        checkpointer: LangGraph checkpointer，用于多轮对话的上下文持久化。
        model: 覆盖默认模型（测试时注入假模型用）。
        system_prompt: 组装好的系统提示词。
        allowed_tool_keys: 限定暴露给模型的工具（意图层检索后的候选集）；
            为 None 表示全量暴露。
        thread_id: 会话 ID，调用记录按它归集。
        trust_mode: 会话信任模式，决定哪些工具需要人工审批。
        skills: 技能源路径（deepAgents 的虚拟路径）。
        skills_dir: 技能导出目录，backend 按 ``/skills/`` 路由挂它。
        memory: 记忆文件路径（AGENTS.md）。
        subagents: 子代理规格；不传就用我们自带的通用子代理。
        allow_interrupt: 是否允许挂起等人批。

            **关掉它的地方只有一处**：子 agent（worker）。worker 没有前端
            交互界面，挂起就永远等不到人批。关掉之后 worker 里的破坏性命令
            也无人拦 —— 防线只剩"自己的 worktree + 断网沙箱"这一层物理隔离。
            同时 worker 也不带 checkpointer（挂起要靠它才能恢复）。

    Returns:
        编译好的图（``CompiledStateGraph``），用法与 ``create_agent`` 一致：
        ``ainvoke`` / ``astream``。
    """
    from deepagents import create_deep_agent

    # **审批由 deepAgents 的 HITL 负责**（``interrupt_on`` 那一套）。
    # 工具能跑到执行层，就说明人已经批过了 —— 执行层不要再问一遍，
    # 否则"批准了等于没批准"。
    # 必须在 build_tools 之前设：工具构造时就把这个标记读进去了。
    context.approval_handled = True

    # 我们的工具（走能力层执行流水线）。FS_TOOL_NAMES 里的跳过 —— 那些由
    # deepAgents 的文件工具提供，两边都有会出现同名工具。
    tools = await build_tools(specs, context, allowed_tool_keys, skip_names=FS_TOOL_NAMES)

    # 同名能力必须显式报错，不能让 LangGraph 静默覆盖
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            raise ValueError(f"能力名重复：{tool.name}")
        seen.add(tool.name)

    # 治理中间件：工具收敛（意图层选了哪些就只露哪些）、参数级风险判定、留痕。
    governance = CapabilityMiddleware(
        specs,
        allowed_tool_keys=allowed_tool_keys,
        context=context,
        thread_id=thread_id,
        trust_mode=trust_mode,
        interrupt_enabled=allow_interrupt,
    )
    # TodoListMiddleware 提供 write_todos —— deepAgents 的规划能力。
    middleware: list[Any] = [governance, TodoListMiddleware()]

    backend = build_backend(context, skills_dir)

    # 压缩中间件：**同名替换** deepAgents 默认的那份（见 app/context/compaction.py）。
    #
    # 替换而不是另加 —— 另加会变成两份摘要中间件同时跑，谁先谁后说不清。
    # 接管之后阈值按模型窗口走比例，而不是框架的固定"170000 token / 6 条"。
    resolved_model = model or _default_model()
    middleware.append(
        build_compaction_middleware(
            model=resolved_model,
            backend=backend,
        )
    )

    # 技能中间件：**同名替换**框架默认的那份，加上清单配额。
    #
    # 渐进披露本身不动（提示词里只有 name + description，正文按需读）——
    # 要改的只是"清单最多列几个"。技能多的时候每轮几千 token 的清单不划算。
    if skills:
        middleware.append(
            QuotaSkillsMiddleware(
                backend=backend,
                sources=skills,
                max_skills=settings.context_max_skills_in_prompt,
            )
        )

    # 声明级审批闸：哪些工具在执行**之前**就要人批。
    # worker（allow_interrupt=False）不挂闸 —— 它没有界面，挂起就等不到人批。
    interrupt_on = _interrupt_on(specs, trust_mode, allowed_tool_keys) if allow_interrupt else {}
    if subagents is None:
        subagents = _general_subagent(
            governance=governance,
            interrupt_on=interrupt_on,
            model=model,
        )

    # 记忆文件必须先存在，记忆中间件才读得到内容
    if memory:
        _ensure_memory_files(context.session_workspace or context.workspace, memory)

    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=system_prompt or _default_prompt(),
        checkpointer=checkpointer,
        middleware=middleware,
        backend=backend,
        interrupt_on=interrupt_on or None,
        skills=skills,
        memory=memory,
        subagents=subagents,
    )


# 工作区说明文件（AGENTS.md）的初始内容
#
# ⚠️ **这不是记忆模块**。两者容易混：
#
# - **AGENTS.md**（本文件）：工作区级、agent 自己维护、**每轮全量注入**的
#   说明文件 —— 类似项目的 CLAUDE.md，管的是"这个工作区的规矩"；
# - **记忆模块**（app/memory/）：带评分的、按需召回的**经验** —— 管的是
#   "用户偏好、项目约定、已定结论"，而且要经能力层写入。
#
# AGENTS.md 的内容不受预算控制、不做相关性排序，所以只适合放短而稳定的东西；
# 篇幅会长、有时效性的内容应该走记忆模块。
_MEMORY_TEMPLATE = (
    "# 工作区说明\n\n"
    "这个文件记录这个工作区的固定规矩（构建命令、目录约定、注意事项）。\n"
    "只写简短稳定的内容 —— 这里的每一句都会进每轮的提示词。\n"
    "会变的、篇幅长的内容请用 memory_write 写进记忆，由记忆模块按需召回。\n"
)


def _ensure_memory_files(workspace: Path, sources: list[str]) -> None:
    """确保记忆文件存在。

    记忆是"**这个文件**"而不是数据库里的某条记录 —— agent 自己就能改它，
    改完下一轮就生效。文件不存在时要先建一个空壳，否则记忆中间件读不到内容。

    文件名取来源路径的**最后一段** —— 来源可能是 ``/AGENTS.md``（没绑工作目录）
    也可能是 ``/.memory/AGENTS.md``（绑了工作目录，挂在会话目录上），
    两种都落到同一个宿主文件上。
    """
    root = Path(workspace)
    for source in sources:
        name = Path(source.strip("/")).name
        if not name:
            continue
        target = root / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_MEMORY_TEMPLATE, encoding="utf-8")


def _interrupt_on(
    specs: list[CapabilitySpec],
    trust_mode: str,
    allowed_tool_keys: set[str] | None,
) -> dict[str, bool]:
    """算出"哪些工具要人工审批"。

    规则只有一份（``tool_policy.approval_required_tools``）—— 中间件告诉模型的
    那句话、执行层的判定，用的都是它。三处各写一套，批准过了又被拦一次就是这么来的。
    """
    names = approval_required_tools(specs, trust_mode, allowed_tool_keys)
    return {name: True for name in names}


def _general_subagent(
    *,
    governance: CapabilityMiddleware,
    interrupt_on: dict[str, bool],
    model: BaseChatModel | None,
) -> list[Any]:
    """我们自己的通用子代理。

    **为什么不用 deepAgents 自带的那个**：它的中间件是按默认槽位继承的，
    我们的 ``CapabilityMiddleware`` 不在里面 —— 子代理的工具调用就不会经过
    风险判定和留痕。所以这里显式声明一份，让它带上同一套治理。
    """
    spec: dict[str, Any] = {
        "name": GENERAL_SUBAGENT_NAME,
        "description": GENERAL_SUBAGENT_DESCRIPTION,
        "system_prompt": GENERAL_SUBAGENT_PROMPT,
        "middleware": [governance, TodoListMiddleware()],
    }
    if interrupt_on:
        spec["interrupt_on"] = interrupt_on
    if model is not None:
        spec["model"] = model
    return [spec]


def _default_model() -> BaseChatModel:
    from app.agents.llm import get_chat_model

    return get_chat_model()


def _default_prompt() -> str:
    from app.agents.prompts import CODE_AGENT_PROMPT

    return CODE_AGENT_PROMPT
