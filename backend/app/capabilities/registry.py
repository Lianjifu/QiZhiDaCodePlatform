"""能力注册表 —— 统一能力清单的**唯一读取入口**。

编排层拿到的每一份能力清单都只从这里来；内置能力也不例外，它在数据库里
有"出厂证"（origin=builtin），代码里的出厂源只负责首次安装。

三张表聚合成一份统一清单：
- ``tool``        → 原子能力
- ``mcp_server``  → 供给通道
- ``skill``       → 能力组合
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.builtin import list_builtin_specs
from app.capabilities.entry import (
    KIND_MCP,
    KIND_SKILL,
    KIND_TOOL,
    ORIGIN_BUILTIN,
    SOURCE_BUILTIN,
    CapabilityEntry,
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
    async def load(session: AsyncSession) -> list[CapabilityEntry]:
        """加载全部能力（三类成员聚合）。"""
        tool_repo = ToolRepository(session)
        mcp_repo = MCPServerRepository(session)
        skill_repo = SkillRepository(session)

        tools = await tool_repo.list_all()
        mcps = await mcp_repo.list_all()
        skills = await skill_repo.list_all()

        entries: list[CapabilityEntry] = []
        entries.extend(_tool_entry(tool) for tool in tools)
        entries.extend(_mcp_entry(mcp) for mcp in mcps)
        entries.extend(await _skill_entries(skill_repo, skills))
        return entries

    @staticmethod
    async def get(session: AsyncSession, key: str) -> CapabilityEntry | None:
        """按 key 取任意一类成员。"""
        for entry in await CapabilityRegistry.load(session):
            if entry.key == key:
                return entry
        return None

    @staticmethod
    async def sync_builtins(session: AsyncSession) -> int:
        """把出厂规格同步进 tool 表，返回新建的条数。

        只补齐缺失的，不覆盖已有的 —— 运营改过的描述、参数、启停状态都不会被
        启动流程冲掉。删掉过的内置能力也不会被自动装回来（除非代码里新增了）。
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
                        parameters=spec.parameters,
                        enabled=True,
                        origin=ORIGIN_BUILTIN,
                        tags=list(spec.tags),
                        permission=spec.permission,
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
                    tool.parameters = spec.parameters
                if not tool.tags:
                    tool.tags = list(spec.tags)
        await session.flush()
        return created


def _tool_entry(tool: Tool) -> CapabilityEntry:
    return CapabilityEntry(
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
        parameters=dict(tool.parameters or {}),
        locator=_tool_locator(tool),
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


def _mcp_entry(server: MCPServer) -> CapabilityEntry:
    return CapabilityEntry(
        key=server.name,
        kind=KIND_MCP,
        db_id=server.id,
        display_name=server.name,
        description=server.description or "",
        tags=list(server.tags or []),
        permission="high",
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


async def _skill_entries(skill_repo: SkillRepository, skills: list[Skill]) -> list[CapabilityEntry]:
    tool_repo = ToolRepository(skill_repo.session)
    entries: list[CapabilityEntry] = []
    for skill in skills:
        tool_ids = await skill_repo.list_tool_ids(skill.id)
        tools = await tool_repo.list_by_ids(tool_ids)
        entries.append(
            CapabilityEntry(
                key=skill.name,
                kind=KIND_SKILL,
                db_id=skill.id,
                display_name=skill.name,
                description=skill.description or "",
                tags=list(skill.tags or []),
                permission="low",
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
    return entries


__all__ = ["CapabilityRegistry"]
