"""Agent 服务：平台侧的业务入口，负责工作区分配与 agent 生命周期。

分层职责：
- `app/capabilities/` 管"能力清单与怎么执行"；
- 这里管"用哪个模型""给这次会话分配哪个工作区""上下文怎么存""错误怎么翻译成 HTTP"。
"""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpointer import open_checkpointer
from app.agents.llm import build_model_for_provider
from app.agents.runner import AgentRunner, AgentRunResult
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.registration.registry import CapabilityRegistry
from app.capabilities.registration.spec import KIND_TOOL, SOURCE_MCP, CapabilitySpec
from app.core.config import settings
from app.core.exceptions import AgentError, AppError, NotFoundError, ValidationError
from app.repositories.llm_provider import LLMProviderRepository
from app.repositories.mcp_server import MCPServerRepository

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
        """每个会话一个独立工作区，目录名做安全过滤。"""
        safe = "".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            raise ValidationError("thread_id 不能为空")
        workspace = self.workspace_base / safe
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

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

    async def chat(
        self,
        message: str,
        thread_id: str,
        provider_id: int | None = None,
        skill_ids: list[int] | None = None,
    ) -> AgentRunResult:
        """跑一轮对话，返回最终回复。"""
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._runner or AgentRunner(checkpointer=checkpointer, model=model)
                result = await runner.run(
                    message,
                    thread_id,
                    workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
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

    async def stream(
        self,
        message: str,
        thread_id: str,
        provider_id: int | None = None,
        skill_ids: list[int] | None = None,
    ) -> AsyncIterator[str]:
        """流式跑一轮对话，逐段产出文本增量。"""
        workspace = self.resolve_workspace(thread_id)
        async with open_checkpointer() as checkpointer:
            try:
                model = await self._resolve_model(provider_id)
                specs = await self.load_capabilities()
                context = await self._build_context(workspace, specs, thread_id)
                runner = self._runner or AgentRunner(checkpointer=checkpointer, model=model)
                async for text in runner.stream(
                    message,
                    thread_id,
                    workspace,
                    specs=specs,
                    context=context,
                    skill_ids=skill_ids,
                ):
                    yield text
                # 执行层写的调用记录要落库
                if self.session is not None:
                    await self.session.commit()
            except AppError:
                raise
            except Exception as exc:
                # SSE 一旦开始推数据就改不了 HTTP 状态码了，只能把错误作为一帧推出去
                logger.exception("agent 流式执行失败 thread_id=%s", thread_id)
                yield f"[ERROR] {_readable_error(exc)}"
