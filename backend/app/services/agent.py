"""Agent 服务：平台侧的业务入口，负责工作区分配与 agent 生命周期。

分层职责：
- `app/capabilities/` 管"能力清单与怎么执行"；
- 这里管"用哪个模型""给这次会话分配哪个工作区""上下文怎么存""错误怎么翻译成 HTTP"。
"""

import json
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpointer import open_checkpointer
from app.agents.llm import build_model_for_provider
from app.agents.permissions import (
    clear_permission_mode,
    resolve_permission_mode,
    set_permission_mode,
)
from app.agents.runner import MEMORY_FILE, STATUS_FAILED, AgentRunner, AgentRunResult, model_window
from app.agents.status import (
    build_collaboration,
    build_connectors,
    build_context_usage,
    build_environment,
    build_status,
    collect_outputs,
    sum_changes,
)
from app.agents.tasks import REGISTRY
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import KIND_MCP, KIND_TOOL, SOURCE_MCP, CapabilitySpec
from app.context.budget import FALLBACK_CONTEXT_WINDOW
from app.context.compaction import TRIGGER_FRACTION
from app.core.config import settings
from app.core.exceptions import AgentError, AppError, NotFoundError, ValidationError
from app.core.workspace import resolve_workspace
from app.environments.service import EnvironmentService
from app.environments.store import save_binding
from app.git.service import GitService
from app.repositories.invocation import InvocationRepository
from app.repositories.llm_provider import LLMProviderRepository
from app.repositories.mcp_server import MCPServerRepository
from app.schemas.agent import (
    AgentForkOut,
    AgentHistoryOut,
    AgentStreamChunk,
    CollaborationOut,
    RunningTaskOut,
    SessionStatusOut,
    WorktreeOut,
)

logger = logging.getLogger(__name__)


def _copy_workspace(source: Path, target: Path) -> None:
    """把工作区里的文件复制到新会话的工作区。

    分支只带对话历史是不够的 —— 文件是 agent 的工作对象，新会话得看得见。

    **复制失败不算致命** —— 新会话照样能聊，只是工作区是空的。
    """
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError as exc:
        logger.warning("复制工作区失败 %s → %s: %s", source, target, exc)


def _inherit_binding(source_thread_id: str, target_thread_id: str) -> None:
    """把工作目录的绑定也带进新会话。

    分支出来的会话跟原会话是同一件事的两条线 —— 得在同一个目录里干活。
    """
    binding = EnvironmentService().current(source_thread_id)
    if binding is None:
        return
    save_binding(target_thread_id, binding)


def _readable_error(exc: Exception) -> str:
    """把底层异常翻译成用户看得懂的提示。

    模型厂商的报错对普通用户不友好（"Missing credentials" 这种），
    但对排障有用 —— 所以原样信息写日志，对外只给可操作的提示。
    """
    text = str(exc)
    lowered = text.lower()
    if "missing credentials" in lowered or "api_key" in lowered:
        return "模型凭据缺失：请在 .env 中配置 LLM_API_KEY（或对应厂商的环境变量）"
    if "connection" in lowered and ("refused" in lowered or "error" in lowered):
        return f"无法连接模型服务：{text or exc.__class__.__name__}"
    if "does not match number of hanging tool calls" in lowered:
        # HITL 的"决定数 ≠ 挂起数"。修好了不会再犯，但**已经挂掉的会话救不回来**
        # —— 那个错误的恢复值已经写进 checkpoint，再批准多少次都还是它。
        # 唯一出路是删掉这一轮重来（界面上是「重新生成」）。
        return (
            "这一轮的批准没有生效（挂起的调用数与批准数对不上）。"
            "点这条消息上的「重新生成」重跑这一轮即可。"
        )
    return f"智能体执行失败：{text or exc.__class__.__name__}"


def _specs_text(specs: list[CapabilitySpec]) -> tuple[str, str, list[str]]:
    """能力声明里的工具定义、MCP 工具定义、以及 MCP 工具的名字。

    只量"模型看到的那部分"：名字、描述、参数 schema。
    **不构造真正的工具** —— 那要去连 MCP 服务器，面板不该等它。
    """
    tools: list[str] = []
    mcp: list[str] = []
    mcp_items: list[str] = []
    for spec in specs:
        if spec.kind == KIND_MCP:
            mcp_items.append(spec.key)
            mcp.append(
                "\n".join(
                    [
                        spec.key,
                        spec.description,
                        json.dumps(spec.input_schema, ensure_ascii=False, default=str),
                    ]
                )
            )
            continue
        if spec.kind != KIND_TOOL or not spec.enabled:
            continue
        tools.append(
            "\n".join(
                [
                    spec.key,
                    spec.description,
                    json.dumps(spec.input_schema, ensure_ascii=False, default=str),
                ]
            )
        )
    return "\n".join(tools), "\n".join(mcp), mcp_items


def _status_system_prompt(workspace: Path, specs: list[CapabilitySpec]) -> str:
    """这次会话的系统提示词（估算用）。

    与 runner 装配的是同一套东西：基础提示 + 策略说明 + 上传件清单。
    """
    from app.agents.prompts import CODE_AGENT_PROMPT
    from app.capabilities.intent.middleware import build_policy_note
    from app.context.service import ContextRequest, build_context_service
    from app.files.attachments import build_uploads_note

    assembled = build_context_service().assemble(
        ContextRequest(
            base_prompt=CODE_AGENT_PROMPT,
            workspace=workspace,
            policy_note=build_policy_note(specs, settings.capability_trust_mode),
            uploads_note=build_uploads_note(workspace),
        )
    )
    return assembled.text


def _skills_text(workspace: Path) -> str:
    """技能正文（导出到工作区旁边的那份快照）。

    没有导出过就是空的 —— 技能是每轮会话才导出的。
    """
    skills_dir = Path(workspace).parent / ".skills" / Path(workspace).name
    if not skills_dir.exists():
        return ""
    parts: list[str] = []
    for path in sorted(skills_dir.rglob("SKILL.md")):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:  # pragma: no cover - 读不到就当没有
            continue
    return "\n".join(parts)


def _memory_text(workspace: Path) -> tuple[str, list[str]]:
    """记忆文件（工作区里的 ``AGENTS.md``）与它们的名字。"""
    path = Path(workspace) / MEMORY_FILE
    if not path.exists():
        return "", []
    try:
        return path.read_text(encoding="utf-8", errors="replace"), [MEMORY_FILE]
    except OSError:  # pragma: no cover
        return "", []


class AgentService:
    """编码 agent 的业务服务。"""

    def __init__(
        self,
        session: AsyncSession | None = None,
        runner: AgentRunner | None = None,
    ) -> None:
        self.session = session
        self._runner = runner

    @property
    def workspace_base(self) -> Path:
        return Path(settings.workspace_root).resolve()

    def resolve_workspace(self, thread_id: str) -> Path:
        """每个会话一个独立工作区。

        目录名的映射规则（安全过滤 + 哈希后缀，保证一一对应）集中在
        ``app/core/workspace.py`` —— 文件接口也要用同一套，不能各写一份。
        """
        return resolve_workspace(thread_id)

    def resolve_agent_workspace(self, thread_id: str) -> tuple[Path, Path]:
        """这个会话的（agent 工作目录, 会话工作区）。

        **绑了工作目录之后两者不是一个地方**：

        - agent 工作目录 = 用户绑的环境（可能是真实的代码仓库）——
          agent 的文件工具与命令执行都在这里；
        - 会话工作区 = 平台自己的目录 —— 上传件、技能导出、产物、文件页
          看的东西都在这里。**上传件不能落进用户的仓库里。**

        没绑环境时两者相同（原有行为不变）。
        """
        session_workspace = self.resolve_workspace(thread_id)
        binding = EnvironmentService().current(thread_id)
        if binding is None:
            return session_workspace, session_workspace
        target = Path(binding.path)
        if not target.is_dir():
            # 绑定的目录没了（被人删了、仓库被移走）—— 退回会话工作区，
            # 别让整个会话因为一个失效的绑定瘫掉。
            logger.warning("会话 %s 绑定的环境已不存在：%s", thread_id, target)
            return session_workspace, session_workspace
        return target, session_workspace

    def _visible_specs(
        self, specs: list[CapabilitySpec], agent_workspace: Path, session_workspace: Path
    ) -> list[CapabilitySpec]:
        """本次给模型看的能力清单。

        **没绑工作目录时不给 git 与派活的能力** —— 那些能力只在 git 仓库里
        才有意义，给模型一批用不了的工具，它会试了再报错，浪费轮次。
        """
        if Path(agent_workspace).resolve() == Path(session_workspace).resolve():
            return [
                spec
                for spec in specs
                if spec.key != "delegate_worker" and not spec.key.startswith("git_")
            ]
        return specs

    async def _resolve_model(self, provider_id: int | None):
        """按供应商配置构造模型；没有可用配置时返回 None（退回环境变量配置）。"""
        if self.session is None:
            return None
        repo = LLMProviderRepository(self.session)
        if provider_id is not None:
            provider = await repo.get(provider_id)
            if provider is None:
                raise NotFoundError(f"供应商配置 {provider_id} 不存在")
        else:
            provider = await repo.get_default()
        if provider is None:
            return None
        if not provider.is_active:
            raise ValidationError(f"供应商配置 {provider.name} 已停用")
        return build_model_for_provider(provider)

    async def load_capabilities(self) -> list[CapabilitySpec]:
        """统一能力清单 —— 只能从注册层来。"""
        if self.session is None:
            return []
        return await CapabilityRegistry.load(self.session)

    async def _build_context(
        self,
        workspace: Path,
        specs: list[CapabilitySpec],
        thread_id: str = "",
        model: Any = None,
        session_workspace: Path | None = None,
    ) -> ExecutionContext:
        """执行上下文：带上本次要用到的 MCP 供给通道。

        顺带把**能力清单**与**模型对象**也放进去 —— 派子 agent 的能力
        需要它们（worker 要有一份自己的工具清单，用同一个模型）。
        """
        mcp_servers = {}
        if self.session is not None:
            repo = MCPServerRepository(self.session)
            for spec in specs:
                if spec.kind != KIND_TOOL or spec.source_type != SOURCE_MCP:
                    continue
                name = spec.locator.get("server") or ""
                server = await repo.get_by_name(name)
                if server is not None and server.enabled:
                    mcp_servers[server.name] = server
        context = ExecutionContext.build(
            workspace,
            mcp_servers,
            session_workspace=session_workspace,
        )
        context.session = self.session
        context.thread_id = thread_id
        context.specs = list(specs)
        context.model = model
        return context

    def _make_runner(self, checkpointer: Any, model: Any) -> AgentRunner:
        """拿一个绑定了 checkpointer 的 runner。

        注入的 runner（测试用）通常只带了假模型，没带 checkpointer ——
        但审批恢复必须走同一个 checkpointer，否则图接不上（LangGraph 直接
        报 "Cannot use Command(resume=...) without checkpointer"）。
        """
        if self._runner is None:
            return AgentRunner(checkpointer=checkpointer, model=model)
        if self._runner.checkpointer is None:
            self._runner.checkpointer = checkpointer
        return self._runner

    async def chat(
        self,
        message: str,
        thread_id: str,
        provider_id: int | None = None,
        skill_ids: list[int] | None = None,
        attachments: list[str] | None = None,
    ) -> AgentRunResult:
        """跑一轮对话，返回最终回复。

        这一轮可能撞上人工审批 —— 那样返回的结果 ``status`` 是
        ``pending_approval``，``pending`` 里是待审批的条目，
        用 :meth:`approve` / :meth:`reject` 恢复。

        ``attachments`` 是本轮带的附件（虚拟路径，如 ``/uploads/a.png``）。
        图片会内联进用户消息 —— 模型能不能看图取决于模型本身（供应商配置上
        的 ``vision_enabled``），不能看就只把路径告诉 agent。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                result = await runner.run(
                    message,
                    thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                    attachments=attachments,
                    session_workspace=session_workspace,
                )
                # 执行层写的调用记录要落库
                if self.session is not None:
                    await self.session.commit()
                return result
            except AppError:
                raise
            except Exception as exc:
                logger.exception("agent 执行失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    # ---------- 人工审批 ----------

    async def pending_approvals(self, thread_id: str) -> list[dict[str, Any]]:
        """这个会话当前挂着哪些待审批的调用。

        审批状态在 LangGraph 的 checkpoint 里（图挂在哪儿只有它知道），
        不在调用记录表里。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                # **把数据库里配的模型取出来** —— 只读接口也要建图才能读 checkpoint，
                # 而建图必须有模型。不传的话它退回环境变量，`.env` 里没配
                # LLM_API_KEY 时这两个接口就整片 500。
                model = await self._resolve_model(None)
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                return await runner.pending_approvals(
                    thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception("读取待审批列表失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    def set_session_permission(self, thread_id: str, mode: str | None) -> str:
        """设置这个会话的权限模式。

        Args:
            thread_id: 会话 ID。
            mode: ``auto`` / ``manual`` / ``accept_edits`` / ``plan`` /
                ``bypass``；``None`` / ``"default"`` 恢复全局默认。

        Returns:
            设置之后的实际模式。

        Raises:
            AgentError: 给了不支持的取值。
        """
        try:
            return set_permission_mode(thread_id, mode)
        except ValueError as exc:
            raise AgentError(str(exc)) from exc

    async def approve(self, thread_id: str) -> AgentRunResult:
        """批准挂起的调用，恢复图继续跑。

        恢复之后 agent 自己接着往下跑 —— 结果直接回到它的上下文，
        不用等用户再说一句。
        """
        return await self._resume(thread_id, "approve")

    async def reject(self, thread_id: str) -> AgentRunResult:
        """拒绝挂起的调用，恢复图（工具不执行，agent 收到拒绝消息）。"""
        return await self._resume(thread_id, "reject")

    async def _resume(self, thread_id: str, decision: str) -> AgentRunResult:
        """恢复挂起的图。

        Args:
            thread_id: 会话 ID。
            decision: ``accept``（批准）或 ``reject``（拒绝）。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(None)
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                result = await runner.resume(
                    thread_id,
                    agent_workspace,
                    decision=decision,
                    specs=specs,
                    context=context,
                )
                if self.session is not None:
                    await self.session.commit()
                return result
            except AppError:
                raise
            except Exception as exc:
                logger.exception("恢复挂起的调用失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    async def history(self, thread_id: str) -> AgentHistoryOut:
        """读会话历史消息。

        前端刷新页面后靠它把对话还原出来 —— 消息在 checkpoint 里，
        不在前端的 localStorage 里。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                # 同 pending_approvals：把数据库里配的模型传进去，别退回环境变量
                model = await self._resolve_model(None)
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                messages = await runner.history(
                    thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                )
                return AgentHistoryOut(thread_id=thread_id, messages=messages)
            except AppError:
                raise
            except Exception as exc:
                logger.exception("读取会话历史失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    async def clear(self, thread_id: str) -> None:
        """清空这个会话的对话记录。

        删的是 **checkpoint 里的历史**，不是工作区里的文件 —— 上传件和产物
        都留着，下一轮 agent 还能用。
        """
        async with open_checkpointer() as checkpointer:
            try:
                await checkpointer.adelete_thread(thread_id)
            except Exception as exc:
                logger.exception("清空会话失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc
        # 会话级权限模式也一并清掉 —— 会话都没了，放行的口径不该留着
        clear_permission_mode(thread_id)

    async def regenerate(
        self,
        thread_id: str,
        provider_id: int | None = None,
        skill_ids: list[int] | None = None,
    ) -> AsyncIterator[AgentStreamChunk]:
        """把上一轮删掉重跑一遍（流式）。

        事件格式与 :meth:`stream` 完全一致 —— 前端那边不用为它单开一条路。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                async for event in runner.regenerate(
                    thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                ):
                    yield event
                if self.session is not None:
                    await self.session.commit()
            except Exception as exc:
                logger.exception("重新生成失败 thread_id=%s", thread_id)
                text = _readable_error(exc)
                yield AgentStreamChunk(type="error", error=text, text=text)
                yield AgentStreamChunk(type="done", status=STATUS_FAILED)

    async def fork(self, thread_id: str, message_id: str) -> AgentForkOut:
        """从某条消息分支出一个新会话。

        新会话保留到分支点为止的历史，之后可以接着聊 —— 原会话不动。

        **工作区也带过去** —— 只带对话不带文件的话，分支出来的会话是空的，
        agent 看不见之前的代码。
        """
        new_thread_id = f"t_{uuid4().hex[:16]}"
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        _copy_workspace(session_workspace, self.resolve_workspace(new_thread_id))
        # 绑定了工作目录的话，新会话**继承同一个环境** —— 分支出来的会话
        # 还得在同一个仓库里干活，不然它跟原会话就不是一回事了。
        _inherit_binding(thread_id, new_thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                model = await self._resolve_model(None)
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                kept = await runner.fork(
                    thread_id,
                    message_id,
                    new_thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                )
                if self.session is not None:
                    await self.session.commit()
                return AgentForkOut(
                    thread_id=new_thread_id,
                    source_thread_id=thread_id,
                    message_id=message_id,
                    kept_messages=kept,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception("分支会话失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    async def status(self, thread_id: str) -> SessionStatusOut:
        """这个会话的当前状态。

        给前端那个状态面板用：环境、上下文占用、产出、改动量、接进来的东西。

        上下文占用是**估算**（4 字符约 1 token）—— 见 :mod:`app.agents.status`。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                # 同 history：把数据库里配的模型传进去
                model = await self._resolve_model(None)
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)

                provider = await self._default_provider()
                model_name = provider.default_model if provider else settings.llm_model
                # **供应商报类型，不报名字** —— 名字是用户自己起的标签
                # （"DeepSeek（本地调试）"），类型才是"哪家的模型"。
                # 环境变量兜底时也一样报类型。
                provider_name = (
                    (provider.provider or provider.name)
                    if provider
                    else settings.llm_provider
                )
                # 窗口：模型 profile 里的最准（供应商配置可能没填）
                limit = model_window(model) or int(getattr(provider, "context_window", 0) or 0)

                history = await self._history_for_status(
                    runner, thread_id, agent_workspace, specs, context
                )
                messages_text = "\n".join(item.content for item in history)

                tools_text, mcp_text, mcp_items = _specs_text(specs)
                memory_text, memory_items = _memory_text(session_workspace)
                window = limit or FALLBACK_CONTEXT_WINDOW
                usage = build_context_usage(
                    limit=window,
                    messages_text=messages_text,
                    system_prompt=_status_system_prompt(session_workspace, specs),
                    tools_text=tools_text,
                    mcp_text=mcp_text,
                    skills_text=_skills_text(session_workspace),
                    memory_text=memory_text,
                    mcp_items=mcp_items,
                    memory_items=memory_items,
                    # 压缩缓冲：窗口用到 85% 就触发压缩，那 15% 得一直空着
                    buffer_tokens=int(window * (1 - TRIGGER_FRACTION)),
                    # **没开口就什么都没占** —— 系统提示词和工具定义是每次请求
                    # 都带上的，可请求还没发出去
                    started=bool(history),
                )

                providers: list[Any] = []
                servers: list[Any] = []
                records: list[Any] = []
                if self.session is not None:
                    providers = await LLMProviderRepository(self.session).list_all()
                    servers = await MCPServerRepository(self.session).list_all()
                    records = await InvocationRepository(self.session).list_recent(
                        thread_id, limit=500
                    )

                running, finished = REGISTRY.snapshot(thread_id)
                return build_status(
                    thread_id=thread_id,
                    environment=build_environment(
                        thread_id=thread_id,
                        workspace=agent_workspace,
                        model=model_name,
                        provider=provider_name,
                        trust_mode=resolve_permission_mode(thread_id),
                    ),
                    context=usage,
                    outputs=collect_outputs(session_workspace),
                    changes=sum_changes(records),
                    connectors=build_connectors(providers=providers, mcp_servers=servers),
                    collaboration=self._collaboration(thread_id, agent_workspace),
                    tasks=[
                        RunningTaskOut(
                            tool=item.tool,
                            detail=item.detail,
                            elapsed_ms=item.elapsed_ms,
                        )
                        for item in running
                    ],
                    finished_tasks=finished,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception("读会话状态失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

    async def _default_provider(self):
        """默认的供应商配置；没有就用环境变量里的。"""
        if self.session is None:
            return None
        return await LLMProviderRepository(self.session).get_default()

    def _collaboration(self, thread_id: str, agent_workspace: Path) -> CollaborationOut:
        """协作状态：仓库里的 worktree 与分支。

        **派出去的子 agent 留下的分支不会自动合回主线** —— 这一段就是给
        用户看"还剩哪些分支没收"的。
        """
        binding = EnvironmentService().current(thread_id)
        if binding is None:
            return build_collaboration()
        git = GitService(agent_workspace)
        if not git.is_repo():
            return build_collaboration(
                bound_path=Path(binding.path),
                bound_kind=binding.kind,
                bound_branch=binding.branch,
                bound_repo=binding.repo,
            )
        root = git.repo_root()
        worktrees = [
            WorktreeOut(
                path=item.path,
                branch=item.branch,
                head=item.head[:8],
                kind="repo" if item.path == str(root) else "worktree",
                repo=str(root) if root else None,
            )
            for item in git.worktree_list()
        ]
        return build_collaboration(
            bound_path=Path(binding.path),
            bound_kind=binding.kind,
            bound_branch=binding.branch or git.current_branch(),
            bound_repo=binding.repo,
            worktrees=worktrees,
            branches=git.branch_list(),
        )


    async def _history_for_status(
        self,
        runner,
        thread_id: str,
        workspace: Path,
        specs,
        context,
    ) -> list:
        """读历史给状态面板用。

        **读不到就当没有** —— 面板上的「对话消息」这一项偏小，
        总比整个接口 500 强（没配模型时就是这样：建图要模型）。
        """
        try:
            return await runner.history(
                thread_id, workspace, specs=specs, context=context
            )
        except Exception as exc:
            logger.warning("读会话历史失败（状态面板的消息数会偏小）: %s", exc)
            return []

    async def stream(
        self,
        message: str,
        thread_id: str,
        provider_id: int | None = None,
        skill_ids: list[int] | None = None,
        attachments: list[str] | None = None,
    ) -> AsyncIterator[AgentStreamChunk]:
        """流式跑一轮对话，逐段产出**事件**。

        事件有五种：文本增量 / 工具调用 / 工具结果 / 待审批 / 收尾。
        """
        agent_workspace, session_workspace = self.resolve_agent_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = self._visible_specs(
                    await self.load_capabilities(), agent_workspace, session_workspace
                )
                context = await self._build_context(
                    agent_workspace,
                    specs,
                    thread_id,
                    model=model,
                    session_workspace=session_workspace,
                )
                runner = self._make_runner(checkpointer, model)
                async for event in runner.stream(
                    message,
                    thread_id,
                    agent_workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                    attachments=attachments,
                ):
                    yield event
                # 执行层写的调用记录要落库
                if self.session is not None:
                    await self.session.commit()
            except Exception as exc:
                # SSE 一旦开始推数据就改不了 HTTP 状态码了，只能把错误作为一帧推出去。
                # 必须是 AgentStreamChunk —— 端点会调 model_dump_json()，塞裸字符串会炸，
                # 流直接断在半路，前端就永远停在「思考中…」。
                #
                # **AppError 也走这里**：供应商配置不存在、已停用这些是"用户请求
                # 不对"，但它们照样发生在流已经开始之后 —— 直接抛出去的话连接断掉，
                # 前端既收不到 error 帧也收不到 done 帧，界面上只剩一个空气泡。
                logger.exception("agent 流式执行失败 thread_id=%s", thread_id)
                message = _readable_error(exc)
                yield AgentStreamChunk(type="error", error=message, text=message)
                yield AgentStreamChunk(type="done", status=STATUS_FAILED)
