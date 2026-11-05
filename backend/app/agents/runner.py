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

**决定数 = 挂起的调用数**：模型一轮里并行发好几个工具调用时，它们会一起挂起，
HITL 会校验决定数对不对得上 —— 少给一个就报 "Number of human decisions (1)
does not match number of hanging tool calls (2)"，整轮直接失败。

**恢复时不用再传用户输入**：图从挂起处继续，模型能接着上一次的上下文往下想。
传空字符串只是为了满足 ``ainvoke`` 的签名。

另外，**审批恢复是另一次 HTTP 请求**，可能落在另一个进程上，所以 checkpointer
必须是文件（见 `checkpointer.py`）。
"""

from __future__ import annotations

import asyncio
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
from langgraph.graph.message import RemoveMessage

from app.agents.builder import build_agent
from app.agents.dynamic_context import (
    PLACEHOLDER_MEMORY,
    PLACEHOLDER_REFLECTION,
    PLACEHOLDER_UPLOADS,
    PromptHolder,
)
from app.agents.graph_cache import GRAPH_CACHE, GraphEntry
from app.agents.llm import get_chat_model, model_identity, supports_vision
from app.agents.permissions import resolve_permission_mode
from app.agents.prompts import base_prompt_for, render_prompt_rules
from app.capabilities.backends.assembly import MEMORY_ROUTE
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.risk import assess_call
from app.capabilities.intent.middleware import build_policy_note
from app.capabilities.registration.spec import KIND_SKILL, CapabilitySpec
from app.capabilities.skills_export import export_skills
from app.context.service import ContextRequest, build_context_service
from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
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

# 后台任务的强引用集。
#
# **必须留着** —— 事件循环只持有弱引用，不留着的任务可能在中途被垃圾回收：
# 复盘刚排上就没了，而且**没有任何报错**（最难查的那种故障）。
# 任务结束时自己从集里摘掉（done_callback）。
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

# 记忆文件名：deepAgents 的记忆中间件按这个约定读工作区里的长期记忆
MEMORY_FILE = "AGENTS.md"

# 工具结果摘要的长度上限。
#
# 工具输出可能有几万 token（读了整个仓库），原样塞进 SSE 会把前端撑爆。
# 要全文去查调用记录（/api/v1/invocations/{id}）。
TOOL_RESULT_SUMMARY_MAX = 400

# LangGraph 允许在**调用时**用配置覆盖 checkpointer（配置里的这个键优先于
# 编译时绑定的那个）。
#
# **图要跨请求复用，checkpointer 不能编进去** —— 它是每次请求新开的一个
# SQLite 连接（用完就关），编进去的话复用时就拿着上个请求那个已经关掉的
# 连接写 checkpoint。这个键名是 langgraph 的内部常量，用注释盯着它。
CONFIG_KEY_CHECKPOINTER = "__pregel_checkpointer"


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


@dataclass
class BuiltAgent:
    """组装好的 agent。

    - ``agent`` —— 编译好的图；
    - ``skills`` —— 本次挂载的技能；
    - ``holder`` —— 每轮变化的那两块提示词内容（记忆目录、上传件清单）；
    - ``context`` —— 这次用的执行上下文（复用图时是**被刷新过的那一个**）。
    """

    agent: Any
    skills: list[CapabilitySpec] = field(default_factory=list)
    holder: PromptHolder = field(default_factory=PromptHolder)
    context: ExecutionContext | None = None


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

    def _config(self, thread_id: str) -> dict[str, Any]:
        """调用图时的配置。

        **checkpointer 按请求带，不编进图里** —— 图要跨请求复用，而
        checkpointer 是每次请求新开的一个 SQLite 连接（用完就关）。编进去
        的话，复用时就拿着上个请求那个已经关掉的连接写 checkpoint。

        **recursion_limit 显式给** —— deepAgents 建图时把它绑成了 9999，
        那等于**没有上限**：一个陷在循环里的 agent 能一口气跑几千轮，
        token 和钱都是真的烧掉了。

        换算：``agent_max_steps`` 数的是「模型 ↔ 工具」往返的**轮数**，
        一轮 = 两个 superstep（调模型 + 执行工具），再加收尾那次模型调用。
        所以 ``轮数 × 2 + 余量`` —— 余量给审批恢复（恢复走同一张图，
        再走一遍被挂起的那段）与收尾。

        平台自己的置信度机制只管"反复填错参数"，管不到"反复做无用功
        但每次都成功"的循环 —— 这个上限就是管那个的。
        """
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": settings.agent_max_steps * 2 + 20,
        }
        if self.checkpointer is not None:
            config[CONFIG_KEY_CHECKPOINTER] = self.checkpointer
        return config

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
        session_workspace: Path | None = None,
    ) -> AgentRunResult:
        """跑完一轮，返回最终回复与过程中的工具调用。

        撞上要审批的调用时不会抛异常 —— 图挂起，返回 ``pending_approval``。

        ``attachments`` 是本轮带的附件（虚拟路径）。图片会被内联进用户消息，
        模型这一轮就能看见；能不能内联取决于模型本身支不支持看图。

        ``workspace`` 是 agent 的工作根目录（绑了工作目录就是那个环境）；
        ``session_workspace`` 是会话自己的目录（上传件在这儿）。两者不同时，
        上传件以 ``/uploads`` 路由挂给 agent —— **不能落进用户的仓库里**。
        """
        model = self._resolve_model()
        session_root = Path(session_workspace) if session_workspace else Path(workspace)
        blocks, parsed = build_attachments(
            session_root,
            attachments or [],
            vision_enabled=supports_vision(model),
            max_inline_bytes=settings.max_inline_image_bytes,
            max_attachments=settings.max_attachments_per_turn,
        )
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        await self._fill_holder(built.holder, message, built.context, parsed)
        result = await built.agent.ainvoke(
            {"messages": _input_messages(message, blocks)},
            config=self._config(thread_id),
        )
        return await self._result_from(
            result, built.skills, thread_id, built.context, specs, task=message
        )

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

        数据源是 checkpoint 的 state —— 与 ``_last_user_message`` 同一个
        来源。前端刷新页面后靠它把对话还原出来。

        没有这个会话的 checkpoint 时返回空列表（不是错误 —— 用户还没聊过）。
        """
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            # 只读 —— 不接管这张图的执行上下文
            attach=False,
        )
        try:
            state = await built.agent.aget_state(self._config(thread_id))
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

        **要跟恢复时看到的完全一致** —— 前端按这里的清单逐条给决定，恢复时
        HITL 校验的就是这个数。所以这里用图自己的状态视图（``aget_state``），
        不用 checkpointer 那条路。
        """
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            # 只读 —— 不接管这张图的执行上下文
            attach=False,
        )
        state = await built.agent.aget_state(self._config(thread_id))
        interrupts = getattr(state, "interrupts", ()) or ()
        return [entry for item in interrupts for entry in _pending_entries(item, specs)]

    async def resume(
        self,
        thread_id: str,
        workspace: Path,
        *,
        decision: str,
        decisions: list[str] | None = None,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AgentRunResult:
        """恢复挂起的图（人工批准 / 拒绝之后）。

        恢复之后 agent 自己接着往下跑 —— 不需要用户再说一句。

        ``decision`` 取 ``"approve"`` / ``"reject"``，对应 deepAgents 的恢复载荷。
        **逐条决定**用 ``decisions`` 给出 —— 一条挂起的调用一个决定，按待批清单
        的顺序（与前端看到的 ``pending`` 一致）。

        恢复这一轮**没有新的用户输入**，但记忆目录仍要带 —— agent 还在接着做
        同一件事，没有目录就等于凭空丢了记忆。检索词取会话里最后一条用户
        消息 —— **直接从 checkpoint 里读，不用为此先建一张图**（早先这里建
        两张图：第一张为了读那句话，第二张带着目录重跑）。
        """
        from langgraph.types import Command

        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        # 检索词取会话里最后一条用户消息（这一轮没有新的用户输入）。
        query = await self._last_user_message(built.agent, thread_id)
        await self._fill_holder(built.holder, query, built.context, None)
        # 挂着的调用有几个就给几个决定 —— HITL 校验的是"决定数 = 挂起数"，
        # 少给一个就报 "Number of human decisions (1) does not match number of
        # hanging tool calls (2)"，整轮直接失败。
        interrupts = await self._interrupts_of(built.agent, thread_id)
        if not interrupts:
            # **没有可批的就明说，别静默空转**。
            #
            # 以前这里直接返回 `{"decisions": [...]}`，图上没挂着中断时
            # `Command(resume=...)` 是个空操作 —— 接口返回 200，什么也没发生，
            # 界面表现就是"审批后无法继续"（工具块一直转圈/未完成，看不出
            # 出了什么事）。现在明确报冲突：要么已经批过了，要么权限模式变了
            # 导致那张图不再挂着它。
            raise ConflictError(
                "这个会话现在没有待批准的调用 —— 可能已经批过了，"
                "或者权限模式变了（图不再挂着这条中断）。"
            )
        resume_value = _build_resume_value(interrupts, decision, decisions, specs)
        result = await built.agent.ainvoke(
            Command(resume=resume_value),
            config=self._config(thread_id),
        )
        return await self._result_from(
            result, built.skills, thread_id, built.context, specs, task=query
        )

    async def _result_from(
        self,
        result: dict[str, Any],
        skills: list[Any],
        thread_id: str,
        context: ExecutionContext | None,
        specs: list[CapabilitySpec] | None = None,
        *,
        task: str = "",
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
                pending=[entry for item in interrupts for entry in _pending_entries(item, specs)],
            )

        tool_calls = _collect_tool_calls(turn_messages)
        await _attach_summaries(tool_calls, context, thread_id)
        final = turn_messages[-1] if turn_messages else None
        content = final.content if isinstance(final, AIMessage) else ""
        # **这一轮跑完了** —— 若这轮有失败，排一次复盘（后台，不阻塞这一轮）。
        #
        # 挂起审批的那条路**不排** —— 那一轮还没结束（用户批完还要 resume
        # 接着跑），那时候排就重复计一次。
        self._schedule_retrospective(thread_id, task)
        return AgentRunResult(
            thread_id=thread_id,
            content=content if isinstance(content, str) else str(content),
            steps=len(tool_calls),
            tool_calls=tool_calls,
            skills=[skill.key for skill in skills],
        )

    def _schedule_retrospective(self, thread_id: str, task: str) -> None:
        """排一次复盘（后台跑，不拖慢这一轮）。

        **绝大多数轮次什么都不会发生** —— 没失败就没有复盘，零成本。

        复盘自己开数据库会话：触发它的这个 HTTP 请求的 session 随后就关了。
        """
        if not settings.autonomy_enabled:
            return
        # **延迟导入** —— 服务层反过来依赖 agent 层的话，顶层导入容易成环
        from app.services.retrospective import run_retrospective_in_background

        try:
            job = asyncio.create_task(
                run_retrospective_in_background(thread_id, task, self._resolve_model())
            )
        except RuntimeError:  # pragma: no cover - 没有运行中的事件循环
            logger.debug("排不进复盘任务，跳过")
            return
        except Exception as exc:  # pragma: no cover - 排任务失败不该影响这一轮
            logger.warning("排复盘任务失败: %s", exc)
            return
        # **必须留一个强引用** —— 事件循环只持有弱引用，不留着的任务
        # 可能在中途被垃圾回收：复盘刚排上就没了，而且**没有任何报错**。
        _BACKGROUND_TASKS.add(job)
        job.add_done_callback(_BACKGROUND_TASKS.discard)

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
        session_workspace: Path | None = None,
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
        session_root = Path(session_workspace) if session_workspace else Path(workspace)
        blocks, parsed = build_attachments(
            session_root,
            attachments or [],
            vision_enabled=supports_vision(model),
            max_inline_bytes=settings.max_inline_image_bytes,
            max_attachments=settings.max_attachments_per_turn,
        )
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        await self._fill_holder(built.holder, message, built.context, parsed)
        async for event in self._stream_turn(built.agent, message, thread_id, blocks, specs):
            yield event

    async def _stream_turn(
        self,
        agent: Any,
        message: str,
        thread_id: str,
        blocks: list[dict[str, Any]],
        specs: list[CapabilitySpec] | None = None,
    ) -> AsyncIterator[AgentStreamChunk]:
        """跑一轮并把事件流出去 —— ``stream`` 与 ``regenerate`` 共用。"""
        seen_calls: set[str] = set()
        seen_results: set[str] = set()

        async for mode, data in agent.astream(
            {"messages": _input_messages(message, blocks)},
            config=self._config(thread_id),
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
        pending = await self._pending_from_state(agent, thread_id, specs)
        if pending:
            yield AgentStreamChunk(type="pending_approval", pending=pending)
            yield AgentStreamChunk(type="done", status=STATUS_PENDING_APPROVAL)
        else:
            # 这一轮真跑完了 —— 若这轮有失败，排一次复盘（后台）
            self._schedule_retrospective(thread_id, message)
            yield AgentStreamChunk(type="done", status=STATUS_COMPLETED)

    async def regenerate(
        self,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> AsyncIterator[AgentStreamChunk]:
        """把上一轮删掉重跑一遍。

        **怎么删的**：LangGraph 的 ``update_state`` 支持 ``RemoveMessage`` ——
        按消息 id 把它们从 checkpoint 的历史里摘掉。我们摘掉**最后一条用户
        消息及其之后的所有消息**（助手回复 + 工具消息），然后拿那条用户消息
        重新跑一遍。

        为什么连用户消息一起删：留着它再发一遍，历史里就会出现两条一样的用户
        消息。
        """
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
        )
        config = self._config(thread_id)
        state = await built.agent.aget_state(config)
        messages = list((getattr(state, "values", None) or {}).get("messages") or [])
        last_user = _last_user_index(messages)
        if last_user < 0:
            raise ValidationError("这个会话还没有可以重新生成的消息")

        # 摘掉这一轮：用户消息 + 之后的助手与工具消息
        removals = [
            RemoveMessage(id=str(msg.id))
            for msg in messages[last_user:]
            if getattr(msg, "id", None)
        ]
        if removals:
            await built.agent.aupdate_state(config, {"messages": removals})

        text = _message_text(messages[last_user].content)
        await self._fill_holder(built.holder, text, built.context, None)
        async for event in self._stream_turn(built.agent, text, thread_id, [], specs):
            yield event

    async def fork(
        self,
        thread_id: str,
        message_id: str,
        new_thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None = None,
        context: ExecutionContext | None = None,
        skill_ids: list[int] | None = None,
    ) -> int:
        """从某条消息分支出一个新会话。

        **不搬 checkpoint** —— 新会话直接 ``update_state`` 播种：把原会话的
        消息列表切到分支点之前，写进新会话的第一个 checkpoint。这么做比复制
        checkpoint 表里的 blob 稳（不用碰存储格式），而且新会话就是个干净的
        普通会话。

        Returns:
            新会话里保留了几条消息。
        """
        built = await self._build(
            thread_id,
            workspace,
            specs=specs,
            context=context,
            skill_ids=skill_ids,
            # 只写 checkpoint，不跑工具 —— 不接管执行上下文
            attach=False,
        )
        state = await built.agent.aget_state(self._config(thread_id))
        messages = list((getattr(state, "values", None) or {}).get("messages") or [])
        index = -1
        for position, msg in enumerate(messages):
            if str(getattr(msg, "id", "") or "") == message_id:
                index = position
                break
        if index < 0:
            raise NotFoundError(f"找不到消息 {message_id}")

        kept = messages[: index + 1]
        await built.agent.aupdate_state(
            self._config(new_thread_id),
            {"messages": kept},
        )
        return len(kept)

    async def _interrupts_of(self, agent: Any, thread_id: str) -> list[Any]:
        """当前这一轮挂在哪儿等人批。"""
        try:
            state = await agent.aget_state(self._config(thread_id))
        except Exception:  # pragma: no cover - 读状态失败不该让恢复崩掉
            logger.debug("读会话状态失败，无法确定挂起的调用数")
            return []
        return list(getattr(state, "interrupts", ()) or ())

    async def _pending_from_state(
        self,
        agent: Any,
        thread_id: str,
        specs: list[CapabilitySpec] | None = None,
    ) -> list[PendingApprovalOut]:
        """从 checkpoint 读这一轮挂起的待批条目。"""
        try:
            state = await agent.aget_state(self._config(thread_id))
        except Exception:  # pragma: no cover - 读状态失败不该让流崩掉
            logger.debug("读会话状态失败，无法判断是否挂起审批")
            return []
        interrupts = getattr(state, "interrupts", ()) or ()
        return [entry for item in interrupts for entry in _pending_entries(item, specs)]

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
            from app.memory.long_term_store import long_term_store_for
            from app.memory.service import MemoryService

            # 长期记忆要连 RAG 集合 —— **集合不在就别建它**：每轮都抛一次
            # "集合不存在"，日志会被它刷满，而且白花开销。
            long_term = await long_term_store_for(context.session, lock=context.db_lock)

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

    async def _reflection_block(
        self,
        context: ExecutionContext | None,
    ) -> str:
        """本会话最近几次没成功的调用，渲染成可注入上下文的块。

        **没有会话上下文就跳过** —— 直接调 API 的场景没有"这个会话的历史"。
        查失败也不该让整轮对话挂掉：反思是加分项，不是前提。
        """
        if context is None or context.session is None or not context.thread_id:
            return ""
        from app.agents.reflection import build_reflection_block

        try:
            async with context.db():
                return await build_reflection_block(context.session, context.thread_id)
        except Exception as exc:  # pragma: no cover - 查表失败不该拖垮对话
            logger.warning("取反思记录失败，本轮不带反思: %s", exc)
            return ""

    async def _load_prompt_rules(self, context: ExecutionContext | None) -> list[Any]:
        """启用中的自动积累规则。

        没有数据库会话就当没有规则 —— 读不到规则不该让整轮对话起不来。
        """
        if context is None or context.session is None:
            return []
        from app.repositories.prompt_rule import PromptRuleRepository

        try:
            async with context.db():
                return await PromptRuleRepository(context.session).list_enabled()
        except Exception as exc:  # pragma: no cover - 查表失败不该拖垮对话
            logger.warning("读自动积累的提示词规则失败，本轮不带规则: %s", exc)
            return []

    async def _last_user_message(self, agent: Any, thread_id: str) -> str:
        """这个会话最后一条用户消息。

        恢复挂起的图时没有新的用户输入，但记忆检索仍需要知道"用户在聊什么"
        —— 所以回退到最后一条用户消息。

        只能从**图**上读：消息在 checkpoint 里是**增量**存的（每一轮写一条
        delta），只有图自己会把这些增量拼回完整的历史。
        """
        try:
            state = await agent.aget_state(self._config(thread_id))
        except Exception as exc:  # pragma: no cover - 读状态失败不该拖垮对话
            logger.debug("读会话最后一条用户消息失败: %s", exc)
            return ""
        values = getattr(state, "values", None) or {}
        return _last_user_message_of(values.get("messages") or [])

    async def _fill_holder(
        self,
        holder: PromptHolder,
        message: str,
        context: ExecutionContext | None,
        attachments: list[Attachment] | None,
    ) -> None:
        """把这一轮的内容填进 holder（记忆目录 + 上传件清单 + 反思记录）。

        **每轮都重填** —— 这三块内容每轮都不一样，而图是跨轮复用的。
        """
        memory_block = await self._memory_block(message, context) if context else ""
        reflection_block = await self._reflection_block(context)
        uploads_note = ""
        if context is not None:
            uploads_note = build_uploads_note(
                Path(context.session_workspace or context.workspace),
                attachments=attachments,
                max_entries=settings.uploads_block_max_entries,
            )
        holder.update(
            memory_catalog=memory_block,
            uploads_note=uploads_note,
            reflection=reflection_block,
        )

    async def _build(
        self,
        thread_id: str,
        workspace: Path,
        *,
        specs: list[CapabilitySpec] | None,
        context: ExecutionContext | None,
        skill_ids: list[int] | None,
        attach: bool = True,
    ) -> BuiltAgent:
        """组装 agent —— **能复用就复用**。

        建一张图要一百毫秒上下（十几个工具 + 十来个中间件 + 编译）。同一个
        会话每轮都要建一次，而这一轮里其实什么都没变：工具集、技能、后端、
        权限模式都一样。会变的只有提示词里的两块内容（记忆目录、上传件清单），
        那两块已经挪到调模型时才注（见 ``dynamic_context.py``）—— 所以图可以
        跨轮复用。

        技能与记忆都在这里准备：

        - **技能**：数据库里的技能导出成 ``SKILL.md``，交给 deepAgents 的技能
          中间件 —— 由模型按描述自己挑，不再由我们做关键词检索；
        - **记忆**：工作区里的 ``AGENTS.md``，每次会话前确保它存在；
        - **记忆目录**：每轮召回一次（**不在这里**，在 ``_fill_holder`` 里）；
        - **上传件**：工作区里用户传过的材料，列进上下文（同上）。

        提示词的**装配**交给上下文模块（``app/context/``）—— 它管预算与
        配额。这里只负责把各来源的内容收集齐了交过去。

        ``attach=False`` 表示"只借这张图，不接管它的执行上下文" —— 读历史、
        看待批清单这类**只读**操作用。缓存里的上下文正被某一轮跑着的时候，
        把它的数据库会话换成这次请求的，会让那一轮的调用记录写到别的请求的
        会话里去。
        """
        specs = specs or []
        context = context or ExecutionContext.build(workspace)
        context.thread_id = context.thread_id or thread_id
        model = self._resolve_model()

        # 上传件、技能导出、产物都按**会话目录**算 —— 绑了工作目录之后
        # agent 的根是用户的仓库，这些东西不能落进仓库里。
        session_root = Path(context.session_workspace or workspace)

        # 记忆文件（工作区说明 AGENTS.md）：绑了工作目录之后它挂在
        # ``/.memory`` 路由上（落在会话目录里）；没绑就是根目录下的 AGENTS.md。
        memory_source = f"/{MEMORY_FILE}"
        if session_root.resolve() != Path(context.workspace).resolve():
            memory_source = f"{MEMORY_ROUTE}/{MEMORY_FILE}"

        skills_dir = _skills_dir(session_root)
        # **技能每次都导出** —— 技能**文件**的内容可能变了（用户改了 SKILL.md），
        # 而图里存的是技能**清单**，不是文件内容。
        skill_sources = export_skills(specs, skills_dir, skill_ids)
        skill_specs = [spec for spec in specs if spec.kind == KIND_SKILL and spec.enabled]

        # 信任模式是**会话级**的：用户在审批卡片上点过「本会话内允许执行
        # 命令」之后，这个会话就是 permissive（中档自动放行）。
        trust_mode = resolve_permission_mode(thread_id)

        # **自动积累的提示词规则** —— 复盘学到的行为准则。
        #
        # 它们要进**图**（不是占位符）：规则不常变，而每轮重查一遍不划算。
        # 代价是规则变了必须重建图 —— 所以规则的身份进了缓存键。
        prompt_rules = await self._load_prompt_rules(context)

        key = _graph_key(
            thread_id=thread_id,
            workspace=workspace,
            session_root=session_root,
            specs=specs,
            trust_mode=trust_mode,
            model=model,
            skill_sources=skill_sources,
            memory_source=memory_source,
            context=context,
            prompt_rules=prompt_rules,
        )
        cached = GRAPH_CACHE.get(key)
        if cached is not None:
            if attach:
                # **把每请求都会变的部分换成这次的** —— 数据库会话、能力清单、
                # 模型。不换的话就会往上一个请求那个已经关掉的会话里写记录。
                cached.refresh(
                    session=context.session,
                    specs=specs,
                    model=model,
                    mcp_servers=context.mcp_servers,
                )
            return BuiltAgent(
                agent=cached.agent,
                skills=skill_specs,
                holder=cached.holder,
                context=cached.context,
            )

        holder = PromptHolder()
        # 策略说明：告诉模型本次哪些工具要人批。它由能力层生成，但**注入到
        # 上下文里** —— 挤不出去的话模型会反复触发审批。
        policy_note = build_policy_note(specs, trust_mode)

        # 技能知识**不进系统提示词** —— deepAgents 的技能中间件按渐进披露来：
        # 提示词里只有技能的名字和描述，模型觉得相关了才去读正文。
        # 提前全塞进去就把"按需加载"退化成"每次都全量加载"了。
        #
        # 上传件清单反过来：用户传了文件，agent 得知道在哪。图片本身已经
        # 内联进用户消息了，这里只是告诉 agent 还有哪些**只能读文件**的材料。
        #
        # **记忆目录与上传件清单留占位符** —— 它们每轮都变，编进来这张图就
        # 不能跨轮复用（agent 会一直看着第一轮的旧记忆）。真正的内容在
        # ``_fill_holder`` 里填，调模型时才替换进去。
        assembled = build_context_service().assemble(
            ContextRequest(
                # **按这一轮实际有的能力拼提示词** —— 没绑 git 目录时平台把
                # git_* 与 delegate_worker 剔掉了，提示词不该再教模型用它们。
                base_prompt=base_prompt_for(specs),
                workspace=workspace,
                memory_catalog=PLACEHOLDER_MEMORY,
                policy_note=policy_note,
                uploads_note=PLACEHOLDER_UPLOADS,
                reflection_note=PLACEHOLDER_REFLECTION,
                prompt_rules=render_prompt_rules([rule.rule for rule in prompt_rules]),
                context_window=model_window(model),
            )
        )
        agent = await build_agent(
            specs=specs,
            context=context,
            # **checkpointer 按请求注入，不编进图里**（见 ``_config``）。
            checkpointer=None,
            model=model,
            system_prompt=assembled.text,
            thread_id=thread_id,
            trust_mode=trust_mode,
            skills=skill_sources or None,
            skills_dir=skills_dir if skill_sources else None,
            memory=[memory_source],
            prompt_holder=holder,
        )
        GRAPH_CACHE.put(
            key,
            GraphEntry(
                agent=agent,
                skills=skill_specs,
                context=context,
                holder=holder,
            ),
        )
        return BuiltAgent(
            agent=agent,
            skills=skill_specs,
            holder=holder,
            context=context,
        )


def _graph_key(
    *,
    thread_id: str,
    workspace: Path,
    session_root: Path,
    specs: list[CapabilitySpec],
    trust_mode: str,
    model: Any,
    skill_sources: list[str],
    memory_source: str,
    context: ExecutionContext,
    prompt_rules: list[Any] | None = None,
) -> tuple[Any, ...]:
    """缓存键 —— **必须盖住所有影响图行为的东西**。

    漏掉一项，后果不是"慢一点"，而是"用错了规则"：

    - **权限模式**漏了，用户刚收紧的闸门又被打开；
    - **能力清单**漏了，新装的能力模型看不见；
    - **模型**漏了，换了供应商还在用旧的；
    - **工作区**漏了，文件工具指着别的目录；
    - **自动积累的规则**漏了，复盘刚学到的准则看不见（旧图还在）。
    """
    return (
        thread_id,
        str(workspace),
        str(session_root),
        trust_mode,
        model_identity(model),
        memory_source,
        tuple(skill_sources),
        # **规则的身份也要进键** —— 提示词是编进图的，规则变了不重建图
        # 就还用着旧的那份。带更新时间是因为规则可能被改写（不只是增删）。
        tuple(
            (rule.id, str(rule.updated_at))
            for rule in (prompt_rules or [])
        ),
        tuple(
            (spec.key, spec.kind, bool(spec.enabled), str(spec.risk), spec.source_type)
            for spec in specs
        ),
        context.sandbox_type,
        context.sandbox_image,
        context.sandbox_timeout,
        context.docker_mount,
        context.max_file_bytes,
        # **MCP 通道也要进键** —— 工具是按建图时的那份配置造的，
        # 服务器改了地址或参数却复用旧图，agent 连的就是老地方。
        tuple(
            (
                name,
                server.transport,
                server.command,
                tuple(server.args or ()),
                server.url,
                bool(server.enabled),
            )
            for name, server in sorted(context.mcp_servers.items())
        ),
    )


def _last_user_message_of(messages: list[Any]) -> str:
    """消息列表里最后一条**用户**消息的正文。

    只要用户的消息 —— agent 自己说的话不是记忆检索的词。
    """
    for msg in reversed(messages or []):
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


def _last_user_index(messages: list[Any]) -> int:
    """最后一条用户消息的下标；没有就是 -1。

    「重新生成」要拿它当回退点 —— 从这条往后全删。
    """
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return -1


def _history_entry(msg: Any) -> AgentHistoryMessage | None:
    """checkpoint 里的一条消息 → 历史消息。

    只留 user / assistant / tool 三种 —— 其余的（系统提示、摘要占位）对前端
    没有意义，还会让列表变长。
    """
    if isinstance(msg, HumanMessage):
        return AgentHistoryMessage(
            id=getattr(msg, "id", None),
            role="user",
            content=_message_text(msg.content),
        )
    if isinstance(msg, AIMessage):
        return AgentHistoryMessage(
            id=getattr(msg, "id", None),
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
            id=getattr(msg, "id", None),
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


def _pending_entries(
    interrupt_obj: Any,
    specs: list[CapabilitySpec] | None = None,
) -> list[dict[str, Any]]:
    """把一个中断整理成待审批条目（**一个挂起的调用一条**）。

    两种中断（deepAgents HITL 的声明级、我们自己的参数级）载荷形状不同，
    这里归一成同一种，给前端一个统一的列表。

    **为什么一条挂起的调用要一条**：模型一轮里可以并行发好几个工具调用，
    它们会一起挂起等人批。HITL 的恢复载荷要求"决定数 = 挂起数"，只给一个
    决定就会报 "Number of human decisions (1) does not match number of
    hanging tool calls (2)"。所以这里按条列出来，恢复时也按条给决定。
    """
    payload = getattr(interrupt_obj, "value", interrupt_obj)
    if not isinstance(payload, dict):
        return [{"kind": "unknown", "message": str(payload)}]
    # deepAgents HITL：{"action_requests": [{...}, {...}]}
    requests = payload.get("action_requests")
    if isinstance(requests, list) and requests:
        entries: list[dict[str, Any]] = []
        for item in requests:
            name = item.get("name", "") if isinstance(item, dict) else ""
            args = item.get("args", {}) if isinstance(item, dict) else {}
            # 风险要按**这次调用的参数**算，不能只报工具的基线档位。
            # 一条 `rm -rf` 走的也是 `execute` 工具 —— 报「执行命令 · 中档」
            # 会让人以为只是跑个命令，实际是在删东西。
            assessment = assess_call(name, args if isinstance(args, dict) else {}, specs)
            entries.append(
                {
                    "kind": "declared",
                    "tool": name,
                    "arguments": args,
                    # 带上风险等级：前端要按档位（低/中/高）告诉人"这次为什么危险"
                    "risk": str(assessment.risk),
                    "reasons": assessment.reasons,
                    "message": f"工具 {name} 需要人工批准后才能执行",
                }
            )
        return entries
    # 我们自己的参数级中断
    return [
        {
            "kind": str(payload.get("kind", "param_risk")),
            "tool": str(payload.get("tool", "")),
            "arguments": payload.get("arguments", {}),
            "risk": payload.get("risk", ""),
            "reasons": payload.get("reasons", []),
            "message": str(payload.get("message", "")),
        }
    ]


def _required_decisions(interrupts: list[Any]) -> int:
    """恢复这一轮要给几个决定。

    HITL 要求"决定数 = 挂起的调用数"，给少了报 mismatch，给多了也一样。
    多个中断时取**最大的那个**：每个 HITL 中断各自按自己的挂起数校验，
    取最大值刚好让它们都过。
    """
    count = 1
    for interrupt_obj in interrupts:
        payload = getattr(interrupt_obj, "value", interrupt_obj)
        requests = payload.get("action_requests") if isinstance(payload, dict) else None
        if isinstance(requests, list) and requests:
            count = max(count, len(requests))
    return count


def _build_resume_value(
    interrupts: list[Any],
    default_decision: str,
    decisions: list[str] | None,
    specs: list[CapabilitySpec] | None = None,
) -> Any:
    """构造恢复载荷 —— 逐条决定或一刀切。

    **决定数必须等于挂起数**：HITL 校验的是"决定数 = 挂起的调用数"，
    少给一个就报 "Number of human decisions (1) does not match number of
    hanging tool calls (2)"，整轮直接失败。

    **逐条决定怎么对应**：待批清单（前端看到的 ``pending``）是按
    "中断顺序 → 该中断里的条目顺序" 摊平的。这里按同样的顺序把 ``decisions``
    切回每个中断 —— 所以前端勾第几条，对应的就是清单里的第几条。

    **多个挂起中断时必须按 interrupt id 分别给值**：LangGraph 看到不止一个
    待答中断就会要求 "you must specify the interrupt id when resuming"，
    给一个笼统的值它会直接报错。

    Args:
        interrupts: 当前挂起的全部中断。
        default_decision: 没给逐条决定时，每个都按它来。
        decisions: 逐条决定（与待批清单同序）；``None`` 表示一刀切。
        specs: 能力清单（算风险用）。

    Returns:
        ``Command(resume=...)`` 的参数。

    Raises:
        ValueError: ``decisions`` 的条数与挂起数对不上。
    """
    if not interrupts:
        return {"decisions": [{"type": default_decision}]}

    # 每个中断贡献几条待批条目
    groups: list[tuple[Any, int]] = []
    total = 0
    for item in interrupts:
        count = len(_pending_entries(item, specs))
        groups.append((item, count))
        total += count

    if decisions is None:
        slices = [[default_decision] * count for _, count in groups]
    else:
        if len(decisions) != total:
            raise ValueError(
                f"决定数（{len(decisions)}）与挂起的调用数（{total}）对不上"
            )
        slices = []
        pos = 0
        for _, count in groups:
            slices.append(list(decisions[pos : pos + count]))
            pos += count

    values = [{"decisions": [{"type": one} for one in group]} for group in slices]

    if len(interrupts) == 1:
        return values[0]

    # 多个中断：LangGraph 要求按 interrupt id 分别给值
    # （给一个笼统的值它会报 "you must specify the interrupt id when resuming"）。
    #
    # 但同一个节点里的多个 interrupt 共享同一个 id —— 这时按 id 给值会把决定
    # 发到同一个地方去，宁可退回"一刀切"的写法（那是之前一直能跑通的）。
    ids = [getattr(item, "id", None) for item, _ in groups]
    if any(not one for one in ids) or len(set(ids)) != len(ids):
        return {"decisions": [{"type": default_decision}] * total}
    return {one: value for one, value in zip(ids, values, strict=True)}


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

    **按"工具名 + 参数"配对，不按顺序** —— 工具是并行跑的，记录按**完成**
    顺序写，调用按**模型给的**顺序；两者对不上时按顺序配就会张冠李戴
    （A 工具的成功被贴到 B 工具上）。
    """
    if not tool_calls or context is None or context.session is None:
        return

    from app.repositories.invocation import InvocationRepository

    repo = InvocationRepository(context.session)
    # 多取一些：这一轮的实际调用数可能跟模型给的不一样（重试、失败在记录之前）
    records = await repo.list_recent(thread_id, limit=max(len(tool_calls) * 3, 10))
    if not records:
        return

    unused = list(records)
    for call in tool_calls:
        record = _match_record(call, unused)
        if record is None:
            continue
        unused.remove(record)
        call["invocation_id"] = record.id
        call["ok"] = record.status == "succeeded"
        call["status"] = record.status
        call["error_code"] = record.error_code
        call["duration_ms"] = record.duration_ms
        call["blocked"] = record.status == "blocked"


def _match_record(call: dict[str, Any], candidates: list[Any]) -> Any | None:
    """给一次工具调用找对应的调用记录。

    优先级：工具名与参数都对上 > 只工具名对得上 > 兜底按顺序拿一条。
    """
    name = str(call.get("name") or "")
    args = call.get("args") or {}
    same_name = [item for item in candidates if item.capability_key == name]
    if not same_name:
        return candidates[0] if candidates else None
    for item in same_name:
        payload = item.payload if isinstance(item.payload, dict) else {}
        if payload == args:
            return item
    return same_name[0]


__all__ = [
    "STATUS_COMPLETED",
    "STATUS_PENDING_APPROVAL",
    "STATUS_FAILED",
    "AgentRunResult",
    "AgentRunner",
]
