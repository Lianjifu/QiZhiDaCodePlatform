"""能力注册表 —— 统一能力清单的**唯一读取入口**。

意图层拿到的每一份能力清单都只从这里来；内置能力也不例外，它在数据库里
有"出厂证"（origin=builtin），代码里的出厂源只负责首次安装。

三张表聚合成一份统一清单：
- ``tool``        → 原子能力
- ``mcp_server``  → 供给通道
- ``skill``       → 能力组合

数据库是唯一事实来源：运营改过的描述、风险等级、权限要求都算数，代码里的出厂
清单只负责"首次安装 + 补空字段"。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.registration.builtin import list_builtin_specs
from app.capabilities.registration.policy import Annotations, RetryPolicy
from app.capabilities.registration.spec import (
    KIND_MCP,
    KIND_SKILL,
    KIND_TOOL,
    ORIGIN_BUILTIN,
    SOURCE_BUILTIN,
    CapabilitySpec,
)
from app.models.mcp_server import MCPServer
from app.models.skill import Skill
from app.models.tool import Tool
from app.repositories.mcp_server import MCPServerRepository
from app.repositories.skill import SkillRepository
from app.repositories.tool import ToolRepository


class CapabilityRegistry:
    """统一能力清单的读取与安装。"""

    @staticmethod
    async def load(session: AsyncSession) -> list[CapabilitySpec]:
        """加载全部能力（三类成员聚合）。"""
        tool_repo = ToolRepository(session)
        mcp_repo = MCPServerRepository(session)
        skill_repo = SkillRepository(session)

        tools = await tool_repo.list_all()
        mcps = await mcp_repo.list_all()
        skills = await skill_repo.list_all()

        specs: list[CapabilitySpec] = []
        specs.extend(_tool_spec(tool) for tool in tools)
        specs.extend(_mcp_spec(server) for server in mcps)
        specs.extend(await _skill_specs(skill_repo, skills))
        return specs

    @staticmethod
    async def get(session: AsyncSession, key: str) -> CapabilitySpec | None:
        """按 key 取任意一类成员。"""
        for spec in await CapabilityRegistry.load(session):
            if spec.key == key:
                return spec
        return None

    @staticmethod
    async def sync_builtins(session: AsyncSession) -> int:
        """把出厂规格同步进 tool 表，返回新建的条数。

        只补齐缺失的，不覆盖已有的 —— 运营改过的描述、参数、风险等级、权限要求
        都不会被启动流程冲掉。删掉过的内置能力也不会被自动装回来（除非代码里新增了）。
        """
        tool_repo = ToolRepository(session)
        existing = {tool.name: tool for tool in await tool_repo.list_all()}
        created = 0
        for spec in list_builtin_specs():
            tool = existing.get(spec.key)
            if tool is None:
                session.add(
                    Tool(
                        name=spec.key,
                        display_name=spec.display_name,
                        description=spec.description,
                        type=SOURCE_BUILTIN,
                        source=spec.source,
                        config={},
                        parameters=spec.input_schema,
                        enabled=True,
                        origin=ORIGIN_BUILTIN,
                        tags=list(spec.tags),
                        permission=spec.permission,
                        risk=spec.risk,
                        output_schema=spec.output_schema,
                        timeout_s=spec.timeout_s,
                        retry_policy=spec.retry.to_dict(),
                        annotations=spec.annotations.to_dict(),
                        version=1,
                    )
                )
                created += 1
                continue
            # 只补空字段，保留运营改过的内容
            if tool.origin == ORIGIN_BUILTIN:
                if not tool.description:
                    tool.description = spec.description
                if not tool.parameters:
                    tool.parameters = spec.input_schema
                if not tool.tags:
                    tool.tags = list(spec.tags)
                if not tool.risk:
                    tool.risk = spec.risk
                if not tool.output_schema:
                    tool.output_schema = spec.output_schema
                if not tool.timeout_s:
                    tool.timeout_s = spec.timeout_s
                if not tool.retry_policy:
                    tool.retry_policy = spec.retry.to_dict()
                if not tool.annotations:
                    tool.annotations = spec.annotations.to_dict()
                if tool.permission in ("low", "high", ""):
                    # 老值域迁移后兜底：low→none、high→confirm
                    tool.permission = "none" if tool.permission != "high" else "confirm"
        await session.flush()
        return created


def _tool_spec(tool: Tool) -> CapabilitySpec:
    return CapabilitySpec(
        key=tool.name,
        kind=KIND_TOOL,
        db_id=tool.id,
        display_name=tool.display_name or tool.name,
        description=tool.description,
        tags=list(tool.tags or []),
        permission=tool.permission,
        enabled=tool.enabled,
        origin=tool.origin,
        version=tool.version,
        source_type=tool.type,
        input_schema=dict(tool.parameters or {}),
        output_schema=dict(tool.output_schema) if tool.output_schema else None,
        locator=_tool_locator(tool),
        risk=tool.risk,
        timeout_s=tool.timeout_s or 30,
        retry=RetryPolicy.from_dict(tool.retry_policy),
        annotations=Annotations.from_dict(tool.annotations),
    )


def _tool_locator(tool: Tool) -> dict:
    """执行入口：按来源给出不同的定位信息。"""
    if tool.type == SOURCE_BUILTIN:
        return {"source": tool.source or ""}
    if tool.type == "mcp":
        return {
            "server": tool.source or "",
            "tool_name": (tool.config or {}).get("tool_name", ""),
        }
    # http
    return dict(tool.config or {})


def _mcp_spec(server: MCPServer) -> CapabilitySpec:
    return CapabilitySpec(
        key=server.name,
        kind=KIND_MCP,
        db_id=server.id,
        display_name=server.name,
        description=server.description or "",
        tags=list(server.tags or []),
        permission="confirm",
        enabled=server.enabled,
        origin="user",
        version=server.version,
        locator={
            "transport": server.transport,
            "command": server.command,
            "args": list(server.args or []),
            "url": server.url,
        },
    )


async def _skill_specs(skill_repo: SkillRepository, skills: list[Skill]) -> list[CapabilitySpec]:
    tool_repo = ToolRepository(skill_repo.session)
    specs: list[CapabilitySpec] = []
    for skill in skills:
        tool_ids = await skill_repo.list_tool_ids(skill.id)
        tools = await tool_repo.list_by_ids(tool_ids)
        specs.append(
            CapabilitySpec(
                key=skill.name,
                kind=KIND_SKILL,
                db_id=skill.id,
                display_name=skill.name,
                description=skill.description or "",
                tags=list(skill.tags or []),
                permission="none",
                enabled=skill.enabled,
                origin="builtin" if skill.builtin else "user",
                version=skill.version,
                members=[tool.name for tool in tools],
                knowledge=skill.knowledge,
                checklist=list(skill.checklist or []),
                examples=list(skill.examples or []),
                constraints=skill.constraints or "",
            )
        )
    return specs


__all__ = ["CapabilityRegistry"]
