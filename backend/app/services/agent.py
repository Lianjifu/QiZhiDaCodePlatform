"""Agent 服务：平台侧的业务入口，负责工作区分配与 agent 生命周期。

分层职责：
- `app/capabilities/` 管"能力清单与怎么执行"；
- 这里管"用哪个模型""给这次会话分配哪个工作区""上下文怎么存""错误怎么翻译成 HTTP"。
"""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpointer import open_checkpointer
from app.agents.llm import build_model_for_provider
from app.agents.runner import MEMORY_FILE, STATUS_FAILED, AgentRunner, AgentRunResult, model_window
from app.agents.status import (
    build_connectors,
    build_context_usage,
    build_environment,
    build_status,
    collect_outputs,
    sum_changes,
)
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import KIND_MCP, KIND_TOOL, SOURCE_MCP, CapabilitySpec
from app.context.budget import FALLBACK_CONTEXT_WINDOW
from app.core.config import settings
from app.core.exceptions import AgentError, AppError, NotFoundError, ValidationError
from app.core.workspace import resolve_workspace
from app.repositories.invocation import InvocationRepository
from app.repositories.llm_provider import LLMProviderRepository
from app.repositories.mcp_server import MCPServerRepository
from app.schemas.agent import AgentHistoryOut, AgentStreamChunk, SessionStatusOut

logger = logging.getLogger(__name__)


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
    return f"智能体执行失败：{text or exc.__class__.__name__}"


def _specs_text(specs: list[CapabilitySpec]) -> tuple[str, str]:
    """能力声明里的工具定义与 MCP 工具定义（两段文本）。

    只量"模型看到的那部分"：名字、描述、参数 schema。
    **不构造真正的工具** —— 那要去连 MCP 服务器，面板不该等它。
    """
    tools: list[str] = []
    mcp: list[str] = []
    for spec in specs:
        if spec.kind == KIND_MCP:
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
    return "\n".join(tools), "\n".join(mcp)


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


def _memory_text(workspace: Path) -> str:
    """记忆文件（工作区里的 ``AGENTS.md``）。"""
    path = Path(workspace) / MEMORY_FILE
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return ""


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
        self, workspace: Path, specs: list[CapabilitySpec], thread_id: str = ""
    ) -> ExecutionContext:
        """执行上下文：带上本次要用到的 MCP 供给通道。"""
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
        context = ExecutionContext.build(workspace, mcp_servers)
        context.session = self.session
        context.thread_id = thread_id
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
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, model)
                result = await runner.run(
                    message,
                    thread_id,
                    workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                    attachments=attachments,
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
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, None)
                return await runner.pending_approvals(
                    thread_id,
                    workspace,
                    specs=specs,
                    context=context,
                )
            except AppError:
                raise
            except Exception as exc:
                logger.exception("读取待审批列表失败 thread_id=%s", thread_id)
                raise AgentError(_readable_error(exc)) from exc

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
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(None)
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, model)
                result = await runner.resume(
                    thread_id,
                    workspace,
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
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, None)
                messages = await runner.history(
                    thread_id,
                    workspace,
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

    async def status(self, thread_id: str) -> SessionStatusOut:
        """这个会话的当前状态。

        给前端那个状态面板用：环境、上下文占用、产出、改动量、接进来的东西。

        上下文占用是**估算**（4 字符约 1 token）—— 见 :mod:`app.agents.status`。
        """
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, None)

                provider = await self._default_provider()
                model_name = provider.default_model if provider else settings.llm_model
                provider_name = provider.name if provider else settings.llm_provider
                # 窗口：模型 profile 里的最准（供应商配置可能没填）
                model = await self._resolve_model(None)
                limit = model_window(model) or int(getattr(provider, "context_window", 0) or 0)

                history = await runner.history(
                    thread_id, workspace, specs=specs, context=context
                )
                messages_text = "\n".join(item.content for item in history)

                tools_text, mcp_text = _specs_text(specs)
                usage = build_context_usage(
                    limit=limit or FALLBACK_CONTEXT_WINDOW,
                    messages_text=messages_text,
                    system_prompt=_status_system_prompt(workspace, specs),
                    tools_text=tools_text,
                    mcp_text=mcp_text,
                    skills_text=_skills_text(workspace),
                    memory_text=_memory_text(workspace),
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

                return build_status(
                    thread_id=thread_id,
                    environment=build_environment(
                        thread_id=thread_id,
                        workspace=workspace,
                        model=model_name,
                        provider=provider_name,
                        trust_mode=settings.capability_trust_mode,
                    ),
                    context=usage,
                    outputs=collect_outputs(workspace),
                    changes=sum_changes(records),
                    connectors=build_connectors(providers=providers, mcp_servers=servers),
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
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._make_runner(checkpointer, model)
                async for event in runner.stream(
                    message,
                    thread_id,
                    workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                    attachments=attachments,
                ):
                    yield event
                # 执行层写的调用记录要落库
                if self.session is not None:
                    await self.session.commit()
            except AppError:
                raise
            except Exception as exc:
                # SSE 一旦开始推数据就改不了 HTTP 状态码了，只能把错误作为一帧推出去。
                # 必须是 AgentStreamChunk —— 端点会调 model_dump_json()，塞裸字符串会炸，
                # 流直接断在半路，前端就永远停在「思考中…」。
                logger.exception("agent 流式执行失败 thread_id=%s", thread_id)
                message = _readable_error(exc)
                yield AgentStreamChunk(type="error", error=message, text=message)
                yield AgentStreamChunk(type="done", status=STATUS_FAILED)
