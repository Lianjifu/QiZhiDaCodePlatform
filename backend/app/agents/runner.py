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
    ToolMessage,
)

from app.agents.builder import build_agent
from app.agents.llm import get_chat_model, supports_vision
from app.agents.prompts import CODE_AGENT_PROMPT
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.intent.middleware import build_policy_note
from app.capabilities.registration.spec import KIND_SKILL, CapabilitySpec
from app.capabilities.skills_export import export_skills
from app.context.service import ContextRequest, build_context_service
from app.core.config import settings
from app.files.attachments import Attachment, build_attachments, build_uploads_note
from app.schemas.agent import (
    AgentHistoryMessage,
    AgentStreamChunk,
    PendingApprovalOut,
    ToolCallOut,
    ToolResultOut,
)

logger = logging.getLogger(__name__)

# 一轮对话的结束状态
STATUS_COMPLETED = "completed"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_FAILED = "failed"

# 记忆文件名：deepAgents 的记忆中间件按这个约定读工作区里的长期记忆
MEMORY_FILE = "AGENTS.md"

# 工具结果摘要的长度上限。
#
# 工具输出可能有几万 token（读了整个仓库），原样塞进 SSE 会把前端撑爆。
# 要全文去查调用记录（/api/v1/invocations/{id}）。
TOOL_RESULT_SUMMARY_MAX = 400


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

    def _resolve_model(self) -> Any:
        """取这次要用的模型对象。

        没注入模型时按环境变量配置建一个 —— 建在这里，不留给 ``build_agent``。

        原因：上下文的窗口与视觉能力都要问模型本人（都在 ``model.profile`` 上）。
        模型是 ``None`` 的话这两个值都拿不到 —— 压缩阈值会退回到保守默认值，
        图片也不会内联。而 ``build_agent`` 内部才做 ``model or _default_model()``，
        那时候外头已经问完了。

        解析结果缓存在 ``self.model`` 上，一个 runner 只建一次。
        """
        if self.model is None:
            self.model = get_chat_model()
        return self.model

    async def run(
        self,
        message: str,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
        attachments: list[str] | None = None,
    ) -> AgentRunResult:
        """跑完一轮，返回最终回复与过程中的工具调用。

        撞上要审批的调用时不会抛异常 —— 图挂起，返回 ``pending_approval``。

        ``attachments`` 是本轮带的附件（虚拟路径）。图片会被内联进用户消息，
        模型这一轮就能看见；能不能内联取决于模型本身支不支持看图。
        """
        model = self._resolve_model()
        blocks, parsed = build_attachments(
            workspace,
            attachments or [],
            vision_enabled=supports_vision(model),
            max_inline_bytes=settings.max_inline_image_bytes,
            max_attachments=settings.max_attachments_per_turn,
        )
        agent, skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            attachments=parsed,
        )
        result = await agent.ainvoke(
            {"messages": _input_messages(message, blocks)},
            config={"configurable": {"thread_id": thread_id}},
        )
        return await self._result_from(result, skills, thread_id, context)

    async def history(
        self,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> list[AgentHistoryMessage]:
        """读这个会话的历史消息。

        数据源同样是 checkpoint 的 state —— 与 ``_last_user_message`` 同一个
        来源。前端刷新页面后靠它把对话还原出来。

        没有这个会话的 checkpoint 时返回空列表（不是错误 —— 用户还没聊过）。
        """
        agent, _skills = await self._build(
            "",
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            # 只是读状态，不跑模型 —— 不必为此付一次召回
            with_memory=False,
        )
        try:
            state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        except Exception as exc:  # pragma: no cover - 没这个会话时不该报错
            logger.debug("读会话历史失败 thread_id=%s: %s", thread_id, exc)
            return []
        values = getattr(state, "values", None) or {}
        entries: list[AgentHistoryMessage] = []
        for msg in values.get("messages") or []:
            entry = _history_entry(msg)
            if entry is not None:
                entries.append(entry)
        return entries

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
        attachments: list[str] | None = None,
    ) -> AsyncIterator[AgentStreamChunk]:
        """流式产出**事件**，供 SSE 推送。

        一帧一种事件：``text``（文本增量）、``tool_call``（模型决定调工具）、
        ``tool_result``（工具跑完）、``pending_approval``（这一轮停在审批上）、
        ``done``（收尾）。

        **为什么要多模式**：LangGraph 的 ``stream_mode="messages"`` 只给文本，
        工具调用在节点的 state 更新里（``updates`` 模式）。两个模式一起开才能
        既看到打字机、又看到工具块。
        """
        model = self._resolve_model()
        blocks, parsed = build_attachments(
            workspace,
            attachments or [],
            vision_enabled=supports_vision(model),
            max_inline_bytes=settings.max_inline_image_bytes,
            max_attachments=settings.max_attachments_per_turn,
        )
        agent, _skills = await self._build(
            message,
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            attachments=parsed,
        )
        seen_calls: set[str] = set()
        seen_results: set[str] = set()

        async for mode, data in agent.astream(
            {"messages": _input_messages(message, blocks)},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, _meta = data
                # 文本增量。流式模型给的是 AIMessageChunk，非流式给完整 AIMessage
                if isinstance(chunk, AIMessageChunk | AIMessage):
                    text = chunk.content
                    if isinstance(text, str) and text:
                        yield AgentStreamChunk(type="text", text=text)
            elif mode == "updates":
                for event in _stream_events(data, seen_calls, seen_results):
                    yield event

        # 收尾：这一轮是跑完了还是停在审批上？
        # 中断的信息在 checkpoint 的 state 里（与 pending_approvals 同一个来源）——
        # 流里不一定带得全，所以这里直接读状态。
        pending = await self._pending_from_state(agent, thread_id)
        if pending:
            yield AgentStreamChunk(type="pending_approval", pending=pending)
            yield AgentStreamChunk(type="done", status=STATUS_PENDING_APPROVAL)
        else:
            yield AgentStreamChunk(type="done", status=STATUS_COMPLETED)

    async def _pending_from_state(self, agent: Any, thread_id: str) -> list[PendingApprovalOut]:
        """从 checkpoint 读这一轮挂起的待批条目。"""
        try:
            state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
        except Exception:  # pragma: no cover - 读状态失败不该让流崩掉
            logger.debug("读会话状态失败，无法判断是否挂起审批")
            return []
        interrupts = getattr(state, "interrupts", ()) or ()
        return [_pending_entry(item) for item in interrupts]

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
                db_lock=context.db_lock,
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
            text = _message_text(content)
            if text.strip():
                return text
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
        attachments: list[Attachment] | None = None,
    ):
        """组装 agent。

        技能与记忆都在这里准备：

        - **技能**：数据库里的技能导出成 ``SKILL.md``，交给 deepAgents 的技能
          中间件 —— 由模型按描述自己挑，不再由我们做关键词检索；
        - **记忆**：工作区里的 ``AGENTS.md``，每次会话前确保它存在；
        - **记忆目录**：每轮召回一次，注入系统提示词（``with_memory=False`` 时跳过）；
        - **上传件**：工作区里用户传过的材料，列进上下文。

        提示词的**装配**交给上下文模块（``app/context/``）—— 它管预算与
        配额。这里只负责把各来源的内容收集齐了交过去。

        ``memory_query`` 是记忆检索用的词；不给就用本轮的 ``message``。
        恢复挂起的图那一轮没有新的用户输入，调用方会显式传入最后一条用户消息。
        """
        specs = specs or []
        context = context or ExecutionContext.build(workspace)
        context.thread_id = context.thread_id or thread_id
        model = self._resolve_model()

        skills_dir = _skills_dir(workspace)
        skill_sources = export_skills(specs, skills_dir, skill_ids)
        skill_specs = [spec for spec in specs if spec.kind == KIND_SKILL and spec.enabled]

        memory_block = ""
        if with_memory:
            memory_block = await self._memory_block(memory_query or message, context)

        # 策略说明：告诉模型本次哪些工具要人批。它由能力层生成，但**注入到
        # 上下文里** —— 挤不出去的话模型会反复触发审批。
        policy_note = build_policy_note(specs, settings.capability_trust_mode)

        # 技能知识**不进系统提示词** —— deepAgents 的技能中间件按渐进披露来：
        # 提示词里只有技能的名字和描述，模型觉得相关了才去读正文。
        # 提前全塞进去就把"按需加载"退化成"每次都全量加载"了。
        #
        # 上传件清单反过来：用户传了文件，agent 得知道在哪。图片本身已经
        # 内联进用户消息了，这里只是告诉 agent 还有哪些**只能读文件**的材料。
        uploads_note = build_uploads_note(
            workspace,
            attachments=attachments,
            max_entries=settings.uploads_block_max_entries,
        )
        assembled = build_context_service().assemble(
            ContextRequest(
                base_prompt=CODE_AGENT_PROMPT,
                workspace=workspace,
                memory_catalog=memory_block,
                policy_note=policy_note,
                uploads_note=uploads_note,
                context_window=model_window(model),
            )
        )
        agent = await build_agent(
            specs=specs,
            context=context,
            checkpointer=self.checkpointer,
            model=model,
            system_prompt=assembled.text,
            thread_id=thread_id,
            trust_mode=settings.capability_trust_mode,
            skills=skill_sources or None,
            skills_dir=skills_dir if skill_sources else None,
            memory=[f"/{MEMORY_FILE}"],
        )
        return agent, skill_specs


def _history_entry(msg: Any) -> AgentHistoryMessage | None:
    """checkpoint 里的一条消息 → 历史消息。

    只留 user / assistant / tool 三种 —— 其余的（系统提示、摘要占位）对前端
    没有意义，还会让列表变长。
    """
    if isinstance(msg, HumanMessage):
        return AgentHistoryMessage(role="user", content=_message_text(msg.content))
    if isinstance(msg, AIMessage):
        return AgentHistoryMessage(
            role="assistant",
            content=_message_text(msg.content),
            tool_calls=[
                ToolCallOut(
                    name=str(call.get("name") or ""),
                    args=dict(call.get("args") or {}),
                    tool_call_id=str(call.get("id") or "") or None,
                )
                for call in (getattr(msg, "tool_calls", None) or [])
            ],
        )
    if isinstance(msg, ToolMessage):
        diff = _diff_of(msg)
        return AgentHistoryMessage(
            role="tool",
            content=_message_text(msg.content),
            name=str(getattr(msg, "name", "") or "") or None,
            tool_call_id=str(getattr(msg, "tool_call_id", "") or "") or None,
            added=diff.get("added"),
            removed=diff.get("removed"),
        )
    return None


def _diff_of(msg: Any) -> dict[str, int]:
    """工具消息里带的改动量。

    由治理中间件在执行前后各读一次文件算出来，挂在 ``additional_kwargs`` 上。
    """
    diff = (getattr(msg, "additional_kwargs", None) or {}).get("diff")
    if not isinstance(diff, dict):
        return {}
    added = diff.get("added")
    removed = diff.get("removed")
    return {
        "added": added if isinstance(added, int) else 0,
        "removed": removed if isinstance(removed, int) else 0,
    }


def _stream_events(
    data: Any,
    seen_calls: set[str],
    seen_results: set[str],
) -> list[AgentStreamChunk]:
    """把 updates 模式的节点更新转成事件。

    updates 模式给的是「每个节点跑完后 state 变了什么」：

    - **agent 节点** → AIMessage，里面带 ``tool_calls`` —— 模型决定调工具了；
    - **tools 节点** → ToolMessage —— 工具跑完了。

    两个集合用来去重：同一个节点更新可能重复送达（比如图重试），同一个工具
    调用也只该亮一次。
    """
    events: list[AgentStreamChunk] = []
    if not isinstance(data, dict):
        return events
    for node_update in data.values():
        if not isinstance(node_update, dict):
            continue
        for msg in node_update.get("messages") or []:
            if isinstance(msg, AIMessage):
                for call in getattr(msg, "tool_calls", None) or []:
                    call_id = str(call.get("id") or "")
                    if call_id and call_id in seen_calls:
                        continue
                    seen_calls.add(call_id)
                    events.append(
                        AgentStreamChunk(
                            type="tool_call",
                            tool_call=ToolCallOut(
                                name=str(call.get("name") or ""),
                                args=dict(call.get("args") or {}),
                                tool_call_id=call_id or None,
                            ),
                        )
                    )
            elif isinstance(msg, ToolMessage):
                call_id = str(getattr(msg, "tool_call_id", "") or "")
                if call_id and call_id in seen_results:
                    continue
                seen_results.add(call_id)
                status = str(getattr(msg, "status", "") or "succeeded")
                diff = _diff_of(msg)
                events.append(
                    AgentStreamChunk(
                        type="tool_result",
                        tool_result=ToolResultOut(
                            tool_call_id=call_id,
                            name=str(getattr(msg, "name", "") or ""),
                            ok=status != "error",
                            status=status,
                            summary=_summarize_tool_output(msg.content),
                            added=diff.get("added"),
                            removed=diff.get("removed"),
                        ),
                    )
                )
    return events


def _summarize_tool_output(content: Any) -> str:
    """工具输出压成一行摘要。"""
    if isinstance(content, list):
        # 多模态内容块：只留文本
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(parts)
    else:
        text = str(content or "")
    text = text.strip()
    if len(text) <= TOOL_RESULT_SUMMARY_MAX:
        return text
    return text[:TOOL_RESULT_SUMMARY_MAX] + "…"


def model_window(model: Any) -> int:
    """取模型的上下文窗口（token）；取不到就是 0（未知）。

    窗口决定上下文预算与压缩阈值。模型对象上的 ``profile`` 是唯一来源 ——
    ``app/agents/llm.py`` 在构造模型时会按供应商配置把窗口挂上去。
    """
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return 0
    value = profile.get("max_input_tokens")
    return int(value) if isinstance(value, int) and value > 0 else 0


def _input_messages(message: str, image_blocks: list[dict] | None = None) -> list[Any]:
    """本轮输入的消息列表。

    **有图片时用列表内容**（text 块 + image 块）—— LangChain 会把 image 块
    转成供应商侧的 ``image_url``，模型这一轮就能看见图。没有图片时保持纯
    字符串，行为与从前完全一致。
    """
    if not image_blocks:
        return [HumanMessage(content=message)]
    content: list[Any] = []
    if message.strip():
        content.append({"type": "text", "text": message})
    content.extend(image_blocks)
    return [HumanMessage(content=content)]


def _message_text(content: Any) -> str:
    """取一条消息里的文本。

    带图片的消息内容是**列表**（text 块 + image 块）—— 只取 text 块，
    图片块不是文字，拿去当检索词只会得到一堆乱码。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if isinstance(part, str))
    return ""


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
                    # 前端拿它对上工具块 —— 审批恢复后要把结果填回同一个块
                    "tool_call_id": str(call.get("id") or "") or None,
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
    "STATUS_FAILED",
    "AgentRunResult",
    "AgentRunner",
]
