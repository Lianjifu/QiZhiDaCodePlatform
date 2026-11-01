"""Agent 执行器：把一次用户输入跑成一轮完整的"思考 + 调工具"循环。

这一层只做编排，不碰数据库、不做鉴权 —— 那是 Service 层的事。

## 一轮对话的三种状态

```text
run()  ──→ completed            模型给了最终回复，这一轮结束
       └─→ pending_approval    中途撞上人工审批，图挂在 checkpoint 里，本轮结束
                    │
                    │  前端把决定发给 resume()
                    ▼
             resumed            agent 自己接着往下跑，不需要用户再说一句
```

**pending_approval 和 completed 都是"一轮正常结束"**，区别只是这一轮有没有
产出最终回复。pending 时 ``content`` 是空的，待批清单在 ``pending`` 里。

## 恢复的约定

恢复必须用**同一个 ``thread_id``** —— 图挂在 checkpoint 里，换了 thread_id
就找不回来了。恢复时发的是 deepAgents 约定的载荷：

```python
Command(resume={"decisions": [{"type": "approve"}]})   # 或 "reject"
```

**恢复时不用再传用户输入**：图从挂起处继续，模型能接着上一次的上下文往下想。
传空字符串只是为了满足 ``ainvoke`` 的签名。

另外，**审批恢复是另一次 HTTP 请求**，可能落在另一个进程上，所以 checkpointer
必须是文件（见 `checkpointer.py`）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
)

from app.agents.builder import build_agent
from app.agents.prompts import CODE_AGENT_PROMPT
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.prompt import assemble_prompt
from app.capabilities.registration.spec import KIND_SKILL, CapabilitySpec
from app.capabilities.skills_export import export_skills
from app.core.config import settings

logger = logging.getLogger(__name__)

# 一轮对话的结束状态
STATUS_COMPLETED = "completed"
STATUS_PENDING_APPROVAL = "pending_approval"

# 记忆文件名：deepAgents 的记忆中间件按这个约定读工作区里的长期记忆
MEMORY_FILE = "AGENTS.md"


@dataclass
class AgentRunResult:
    """一轮 agent 执行的结果。

    - ``content`` —— 最终回复文本（pending 时为空）；
    - ``steps`` —— 这一轮的工具调用次数；
    - ``tool_calls`` —— 每次调用的名字、参数、结果摘要（状态 / 耗时 / 记录 ID）；
    - ``skills`` —— 本会话挂载的技能；
    - ``status`` —— ``completed`` 或 ``pending_approval``；
    - ``pending`` —— 待审批条目（只有 pending 时有值）。
    """

    thread_id: str
    content: str = ""
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    status: str = STATUS_COMPLETED
    # 待审批的条目（status == pending_approval 时有值）
    pending: list[dict[str, Any]] = field(default_factory=list)


class AgentRunner:
    """执行 agent。可注入 checkpointer 与 model（测试时用假模型）。"""

    def __init__(self, checkpointer: Any = None, model: Any = None) -> None:
        self.checkpointer = checkpointer
        self.model = model

    async def run(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AgentRunResult:
        """跑完一轮，返回最终回复与过程中的工具调用。

        撞上要审批的调用时不会抛异常 —— 图挂起，返回 ``pending_approval``。
        """
        agent, skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        result = await agent.ainvoke(
            {"messages": _input_messages(message)},
            config={"configurable": {"thread_id": thread_id}},
        )
        return await self._result_from(result, skills, thread_id, context)

    async def pending_approvals(
        self,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """这个会话当前挂着哪些待审批的调用。

        数据源是 LangGraph 的 checkpoint —— 图挂在哪儿只有它知道。
        用来做"刷新页面后待批列表还在"这类场景。
        """
        agent, _skills = await self._build(
            "",
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            # 只是查状态，不跑模型 —— 不必为此付一次召回
            with_memory=False,
        )
        state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        interrupts = getattr(state, "interrupts", ()) or ()
        return [_pending_entry(item) for item in interrupts]

    async def resume(
        self,
        thread_id: str,
        workspace: Path,
        *,
        decision: str,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AgentRunResult:
        """恢复挂起的图（人工批准 / 拒绝之后）。

        恢复之后 agent 自己接着往下跑 —— 不需要用户再说一句。

        ``decision`` 取 ``"approve"`` / ``"reject"``，对应 deepAgents 的恢复载荷。

        恢复这一轮**没有新的用户输入**，但记忆目录仍要带 —— agent 还在接着做
        同一件事，没有目录就等于凭空丢了记忆。检索词取会话里最后一条用户
        消息，所以这里先造一次图把那条消息读出来，再带着目录重造一次。
        """
        from langgraph.types import Command

        agent, skills = await self._build(
            "",
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            with_memory=False,
        )
        query = await self._last_user_message(agent, thread_id)
        if query:
            agent, skills = await self._build(
                "",
                thread_id,
                workspace,
                specs=specs,
                context=context,
                skill_ids=skill_ids,
                memory_query=query,
            )
        result = await agent.ainvoke(
            Command(resume={"decisions": [{"type": decision}]}),
            config={"configurable": {"thread_id": thread_id}},
        )
        return await self._result_from(result, skills, thread_id, context)

    async def _result_from(
        self,
        result: dict[str, Any],
        skills: list[Any],
        thread_id: str,
        context: ExecutionContext | None,
    ) -> AgentRunResult:
        """把图的结果整理成 ``AgentRunResult``。

        分两条路：有中断 → ``pending_approval``（没有最终回复）；没中断 →
        ``completed``（取最后一条 AI 消息作为回复）。
        """
        messages = result.get("messages", [])
        # 用了 checkpointer 后，state 里带着历史消息；只统计本轮（最后一条用户输入之后）的结果
        start = 0
        for index, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                start = index + 1
        turn_messages = messages[start:] or messages

        # 中途撞上人工审批 → 图挂起了，这一轮没有最终回复
        interrupts = _interrupts(result)
        if interrupts:
            return AgentRunResult(
                thread_id=thread_id,
                content="",
                steps=len(_collect_tool_calls(turn_messages)),
                tool_calls=_collect_tool_calls(turn_messages),
                skills=[skill.key for skill in skills],
                status=STATUS_PENDING_APPROVAL,
                pending=[_pending_entry(item) for item in interrupts],
            )

        tool_calls = _collect_tool_calls(turn_messages)
        await _attach_summaries(tool_calls, context, thread_id)
        final = turn_messages[-1] if turn_messages else None
        content = final.content if isinstance(final, AIMessage) else ""
        return AgentRunResult(
            thread_id=thread_id,
            content=content if isinstance(content, str) else str(content),
            steps=len(tool_calls),
            tool_calls=tool_calls,
            skills=[skill.key for skill in skills],
        )

    async def stream(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AsyncIterator[str]:
        """流式产出模型文本增量，供 SSE 推送。"""
        agent, _skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        async for chunk, _meta in agent.astream(
            {"messages": _input_messages(message)},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="messages",
        ):
            if isinstance(chunk, AIMessageChunk):
                text = chunk.content
                if isinstance(text, str) and text:
                    yield text

    async def _memory_block(
        self,
        message: str,
        context: ExecutionContext,
    ) -> str:
        """召回记忆目录，渲染成可注入上下文的块。

        **每轮自动注入的是目录，不是记忆正文** —— 上下文里只放一行摘要，
        模型觉得相关了再调 ``memory_read`` 取全文。这是渐进式披露的第一层。

        没有数据库会话（测试、纯编排场景）就跳过；有会话但召回失败也不该
        让整轮对话挂掉 —— 记忆是加分项，不是前提。
        """
        if context.session is None or not message.strip():
            return ""
        try:
            from app.memory.long_term_store import LongTermMemoryStore
            from app.memory.service import MemoryService

            # 长期记忆要连 RAG 集合，没配集合名就别建它 —— 否则每轮都会
            # 抛一次"未配置长期记忆集合"，日志全是它。
            long_term = None
            if settings.memory_collection_name:
                long_term = LongTermMemoryStore(context.session)

            service = MemoryService(
                session=context.session,
                thread_id=context.thread_id,
                long_term_store=long_term,
            )
            catalog = await service.recall_catalog(message)
            return catalog.render()
        except Exception as exc:  # pragma: no cover - 召回失败不该拖垮对话
            logger.warning("召回记忆目录失败，本轮不带记忆: %s", exc)
            return ""

    async def _last_user_message(self, agent: Any, thread_id: str) -> str:
        """从图的状态里取这个会话最后一条用户消息。

        恢复挂起的图时没有新的用户输入，但记忆检索仍需要知道"用户在聊什么"
        —— 所以回退到最后一条用户消息。

        只能从**编译好的图**上读状态：LangGraph 把消息存在单独的 blob 表里，
        checkpointer 的 ``aget`` / ``aget_tuple`` 都拿不到它。
        """
        try:
            state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        except Exception as exc:  # pragma: no cover - 读状态失败不该拖垮对话
            logger.debug("读会话最后一条用户消息失败: %s", exc)
            return ""
        values = getattr(state, "values", None) or {}
        for msg in reversed(values.get("messages") or []):
            # 只要**用户**的消息 —— agent 自己说的话不是检索词
            if isinstance(msg, HumanMessage):
                content = msg.content
            elif isinstance(msg, dict) and msg.get("type") == "human":
                content = msg.get("content")
            else:
                continue
            if isinstance(content, str) and content.strip():
                return content
        return ""

    async def _build(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None,
        context: ExecutionContext | None,
        skill_ids: list[int] | None,
        with_memory: bool = True,
        memory_query: str = "",
    ):
        """组装 agent。

        技能与记忆都在这里准备：

        - **技能**：数据库里的技能导出成 ``SKILL.md``，交给 deepAgents 的技能
          中间件 —— 由模型按描述自己挑，不再由我们做关键词检索；
        - **记忆**：工作区里的 ``AGENTS.md``，每次会话前确保它存在；
        - **记忆目录**：每轮召回一次，注入系统提示词（``with_memory=False`` 时跳过）。

        ``memory_query`` 是记忆检索用的词；不给就用本轮的 ``message``。
        恢复挂起的图那一轮没有新的用户输入，调用方会显式传入最后一条用户消息。
        """
        specs = specs or []
        context = context or ExecutionContext.build(workspace)
        context.thread_id = context.thread_id or thread_id

        skills_dir = _skills_dir(workspace)
        skill_sources = export_skills(specs, skills_dir, skill_ids)
        skill_specs = [spec for spec in specs if spec.kind == KIND_SKILL and spec.enabled]

        memory_block = ""
        if with_memory:
            memory_block = await self._memory_block(memory_query or message, context)

        # 技能知识**不进系统提示词** —— deepAgents 的技能中间件按渐进披露来：
        # 提示词里只有技能的名字和描述，模型觉得相关了才去读正文。
        # 提前全塞进去就把"按需加载"退化成"每次都全量加载"了。
        system_prompt = assemble_prompt(
            CODE_AGENT_PROMPT,
            runtime_notes=[
                # 文件工具走的是**虚拟路径**（以 "/" 为工作区根），宿主路径只
                # 在拼命令时用。把宿主路径混进提示词，模型会拿它去调文件工具，
                # 换来一串 path_not_found —— 白烧好几轮。
                f"文件工具的路径是虚拟路径，以 `/` 为工作区根目录；"
                f"宿主路径 {workspace} 只在拼命令时用。"
            ],
            extra_blocks=[memory_block],
        )
        agent = await build_agent(
            specs=specs,
            context=context,
            checkpointer=self.checkpointer,
            model=self.model,
            system_prompt=system_prompt,
            thread_id=thread_id,
            trust_mode=settings.capability_trust_mode,
            skills=skill_sources or None,
            skills_dir=skills_dir if skill_sources else None,
            memory=[f"/{MEMORY_FILE}"],
        )
        return agent, skill_specs


def _input_messages(message: str) -> list[Any]:
    """本轮输入的消息列表。"""
    return [HumanMessage(content=message)]


def _skills_dir(workspace: Path) -> Path:
    """技能导出目录。

    放在工作区**外面**：导出文件是这次会话的快照，agent 的文件工具不该看见
    它们，更不该能改写它们。
    """
    return Path(workspace).parent / ".skills" / Path(workspace).name


def _interrupts(result: dict[str, Any]) -> list[Any]:
    """从图的结果里取出中断载荷。

    LangGraph 把中断放在 ``__interrupt__`` 里（tuple / list 都见过）。
    """
    raw = result.get("__interrupt__")
    if not raw:
        return []
    if isinstance(raw, (tuple, list)):
        return list(raw)
    return [raw]


def _pending_entry(interrupt_obj: Any) -> dict[str, Any]:
    """把一个中断整理成待审批条目。

    两种中断（deepAgents HITL 的声明级、我们自己的参数级）载荷形状不同，
    这里归一成同一种，给前端一个统一的列表。
    """
    payload = getattr(interrupt_obj, "value", interrupt_obj)
    if not isinstance(payload, dict):
        return {"kind": "unknown", "message": str(payload)}
    # deepAgents HITL：{"action_requests": [{...}]}
    requests = payload.get("action_requests")
    if isinstance(requests, list) and requests:
        first = requests[0]
        name = first.get("name", "") if isinstance(first, dict) else ""
        args = first.get("args", {}) if isinstance(first, dict) else {}
        return {
            "kind": "declared",
            "tool": name,
            "arguments": args,
            "message": f"工具 {name} 需要人工批准后才能执行",
        }
    # 我们自己的参数级中断
    return {
        "kind": str(payload.get("kind", "param_risk")),
        "tool": str(payload.get("tool", "")),
        "arguments": payload.get("arguments", {}),
        "risk": payload.get("risk", ""),
        "reasons": payload.get("reasons", []),
        "message": str(payload.get("message", "")),
    }


def _collect_tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    """收集本轮的工具调用（先不带摘要，摘要由 :func:`_attach_summaries` 补）。"""
    tool_calls: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, AIMessage) or not getattr(msg, "tool_calls", None):
            continue
        for call in msg.tool_calls:
            tool_calls.append(
                {
                    "name": str(call.get("name") or ""),
                    "args": dict(call.get("args") or {}),
                }
            )
    return tool_calls


async def _attach_summaries(
    tool_calls: list[dict[str, Any]],
    context: ExecutionContext | None,
    thread_id: str,
) -> None:
    """给工具调用补上执行摘要（成功 / 失败 / 耗时 / 记录 ID）。

    摘要**从调用记录来**，不从工具消息来 —— deepAgents 的工具消息没有我们那套
    artifact 结构，而调用记录是唯一事实来源（执行层与治理中间件都往里写）。

    记录与调用按顺序对上：这一轮的最后 N 条记录就是这一轮的 N 次调用。
    """
    if not tool_calls or context is None or context.session is None:
        return

    from app.repositories.invocation import InvocationRepository

    repo = InvocationRepository(context.session)
    records = await repo.list_recent(thread_id, limit=len(tool_calls))
    if len(records) < len(tool_calls):
        return
    for call, record in zip(tool_calls, records[-len(tool_calls) :], strict=False):
        call["invocation_id"] = record.id
        call["ok"] = record.status == "succeeded"
        call["status"] = record.status
        call["error_code"] = record.error_code
        call["duration_ms"] = record.duration_ms
        call["blocked"] = record.status == "blocked"


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_PENDING_APPROVAL",
    "AgentRunResult",
    "AgentRunner",
]
