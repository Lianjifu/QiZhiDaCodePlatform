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
from app.capabilities.registration.tool_policy import SUPERSEDED_BUILTIN_TOOLS
from app.models.mcp_server import MCPServer
from app.models.skill import Skill
from app.models.tool import Tool
from app.repositories.mcp_server import MCPServerRepository
from app.repositories.skill import SkillRepository
from app.repositories.tool import ToolRepository


class CapabilityRegistry:
    """统一能力清单的读取与安装。

    对外只有三个静态方法：``load``（拿全部）、``get``（按 key 取）、
    ``sync_builtins``（安装出厂能力）。清单本身没有缓存 —— 每次读都走数据库，
    保证运营改完声明立刻生效，不存在"清单过期"这回事。
    """

    @staticmethod
    async def load(session: AsyncSession) -> list[CapabilitySpec]:
        """加载全部能力（三类成员聚合）。

        Args:
            session: 数据库会话。

        Returns:
            统一清单：先是原子能力（tool），再是供给通道（mcp），最后是能力组合（skill）。

        顺序在这里不重要 —— 真正决定模型能用什么的是意图层的收敛。
        """
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
        """按 key 取任意一类成员。

        Args:
            session: 数据库会话。
            key: 能力 key（tool 名 / mcp server 名 / skill 名）。

        Returns:
            找到返回对应 spec，找不到返回 ``None``。

        实现上是"全量加载后线性找"：清单规模是几十到几百条，一次查询换不来
        多少，反而多一套按 key 索引的维护成本。
        """
        for spec in await CapabilityRegistry.load(session):
            if spec.key == key:
                return spec
        return None

    @staticmethod
    async def sync_builtins(session: AsyncSession) -> int:
        """把出厂规格同步进 tool 表，返回新建 + 下线的条数。

        只补齐缺失的，不覆盖已有的 —— 运营改过的描述、参数、风险等级、权限要求
        都不会被启动流程冲掉。删掉过的内置能力也不会被自动装回来（除非代码里新增了）。

        同时下线**已被 deepAgents 接管**的内置能力（见 ``_retire_superseded``）。

        Args:
            session: 数据库会话。

        Returns:
            本次新建 + 下线的内置能力条数。

        "只补空字段"是这里的核心约定：启动是**幂等安装**，不是配置覆盖。
        运营在后台调过 ``web_fetch`` 的风险等级，那下次重启不能被代码里的默认值
        改回去 —— 否则治理策略就成了摆设。
        """
        tool_repo = ToolRepository(session)
        existing = {tool.name: tool for tool in await tool_repo.list_all()}
        created = 0
        for spec in list_builtin_specs():
            tool = existing.get(spec.key)
            if tool is None:
                # 库里没有这条 → 全新安装，出厂声明整体写入
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
                # 用户自建的能力（origin=user）完全不动 —— 出厂源里同名也不该覆盖
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
        retired = _retire_superseded(existing)
        await session.flush()
        return created + retired


def _retire_superseded(existing: dict[str, Tool]) -> int:
    """下线已被 deepAgents 接管的内置能力。

    文件类能力（read_file / write_file / …）和 ``run_bash`` 现在由 deepAgents 的
    文件系统中间件提供 —— 能力名也变了（``list_files`` → ``ls``）。数据库里
    那些老行必须**停用并清空执行入口**，否则：

    - 能力清单里会多出一堆实际不存在的工具；
    - 执行入口指向已经删掉的模块，一旦被调到就 import 失败。

    只处理出厂内置的（``origin=builtin``）—— 用户自建的同名能力不受影响。

    Args:
        existing: 按名字索引的已有工具行。

    Returns:
        本次下线的能力条数。
    """
    retired = 0
    for name in SUPERSEDED_BUILTIN_TOOLS:
        tool = existing.get(name)
        if tool is None or tool.origin != ORIGIN_BUILTIN:
            continue
        if not tool.enabled and not tool.source:
            continue
        tool.enabled = False
        tool.source = ""
        tool.description = "已由 deepAgents 的文件工具接管，保留记录仅供审计"
        retired += 1
    return retired


def _tool_spec(tool: Tool) -> CapabilitySpec:
    """把 ``tool`` 表的一行转成能力声明。

    数据库的列名是平台早期的叫法（``name`` / ``parameters``），声明层叫
    ``key`` / ``input_schema`` —— 转换只发生在这里，别处一律用声明层的名字。
    """
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
    """执行入口：按来源给出不同的定位信息。

    三种来源的 locator 结构完全不同：builtin 是"模块:工厂函数"，
    mcp 是"server 名 + 工具名"，http 是请求模板。执行层的适配器按
    ``source_type`` 选适配器，再按这里的结构取入口。
    """
    if tool.type == SOURCE_BUILTIN:
        return {"source": tool.source or ""}
    if tool.type == "mcp":
        return {
            "server": tool.source or "",
            "tool_name": (tool.config or {}).get("tool_name", ""),
        }
    # http：整个 config 就是请求模板
    return dict(tool.config or {})


def _mcp_spec(server: MCPServer) -> CapabilitySpec:
    """把 ``mcp_server`` 表的一行转成能力声明。

    MCP server 是**供给通道**，不是能力本身 —— 它不执行，通道上的工具才执行。
    权限写死 ``confirm``：走外部 server 意味着平台管不到那侧的副作用。
    """
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
    """把 ``skill`` 表的行转成能力声明，并把关联的原子能力填进 ``members``。

    关联关系存在 ``skill_tool`` 表里（不是 JSON 数组）—— 好处是能反查、
    删工具时能级联解绑，不会留下悬挂 id。
    """
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
