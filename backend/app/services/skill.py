"""技能服务：CRUD + 把技能解析成 agent 可用的工具与提示词。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.mcp_server import MCPServer
from app.models.skill import Skill
from app.repositories.mcp_server import MCPServerRepository
from app.repositories.skill import SkillRepository
from app.repositories.tool import ToolRepository
from app.schemas.common import PageMeta
from app.schemas.skill import SkillCreate, SkillUpdate
from app.services.base import BaseService, PageResult
from app.skills.executor import ToolExecutor
from app.skills.prompt import build_skill_prompt


class SkillService(BaseService[SkillRepository]):
    repository = SkillRepository

    async def get_skill(self, skill_id: int) -> Skill:
        skill = await self.repo.get(skill_id)
        if skill is None:
            raise NotFoundError(f"技能 {skill_id} 不存在")
        return skill

    async def list_skills(
        self, *, page: int = 1, page_size: int = 20, enabled: bool | None = None
    ) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items = await self.repo.list_all()
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        total = len(items)
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        pages = (total + page_size - 1) // page_size
        return PageResult(
            items=window,
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )

    async def list_tool_ids(self, skill_id: int) -> list[int]:
        return await self.repo.list_tool_ids(skill_id)

    async def get_tool_names(self, skill_id: int) -> list[str]:
        """技能关联的工具名（给响应体用）。"""
        tool_repo = ToolRepository(self.session)
        tools = await tool_repo.list_by_ids(await self.repo.list_tool_ids(skill_id))
        return [tool.name for tool in tools]

    async def create_skill(self, payload: SkillCreate) -> Skill:
        if await self.repo.get_by_name(payload.name) is not None:
            raise ConflictError(f"技能 {payload.name} 已存在")
        await self._assert_tools_exist(payload.tool_ids)
        try:
            skill = await self.repo.create(
                name=payload.name,
                description=payload.description,
                prompt=payload.prompt,
                enabled=payload.enabled,
                builtin=False,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"技能 {payload.name} 已存在") from exc
        await self.repo.replace_tools(skill.id, payload.tool_ids)
        await self.session.commit()
        return skill

    async def update_skill(self, skill_id: int, payload: SkillUpdate) -> Skill:
        skill = await self.get_skill(skill_id)
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] and data["name"] != skill.name:
            if await self.repo.get_by_name(data["name"]) is not None:
                raise ConflictError(f"技能 {data['name']} 已存在")
        tool_ids = data.pop("tool_ids", None)
        if tool_ids is not None:
            await self._assert_tools_exist(tool_ids)
        skill = await self.repo.update(skill, **data)
        if tool_ids is not None:
            await self.repo.replace_tools(skill.id, tool_ids)
        await self.session.commit()
        return skill

    async def delete_skill(self, skill_id: int) -> None:
        skill = await self.get_skill(skill_id)
        if skill.builtin:
            raise ConflictError(f"内置技能 {skill.name} 不能删除，只能停用")
        await self.repo.replace_tools(skill.id, [])
        await self.repo.delete(skill)
        await self.session.commit()

    # ---------- 给 agent 用 ----------

    async def resolve_skills(self, skill_ids: list[int], context: dict) -> tuple[list, str]:
        """把技能解析成 (可执行工具列表, 附加提示词)。

        查不到的技能直接报错；单个工具构造失败则跳过，不影响其余工具。
        """
        if not skill_ids:
            return [], ""
        skills: list[Skill] = []
        for skill_id in dict.fromkeys(skill_ids):
            skill = await self.repo.get(skill_id)
            if skill is None:
                raise NotFoundError(f"技能 {skill_id} 不存在")
            if not skill.enabled:
                continue
            skills.append(skill)

        tool_repo = ToolRepository(self.session)
        server_repo = MCPServerRepository(self.session)
        servers: dict[str, MCPServer] = {}
        tool_ids: list[int] = []
        for skill in skills:
            tool_ids.extend(await self.repo.list_tool_ids(skill.id))

        tools = await tool_repo.list_by_ids(list(dict.fromkeys(tool_ids)))
        enabled_tools = [tool for tool in tools if tool.enabled]

        mcp_servers = [tool.source for tool in enabled_tools if tool.type == "mcp"]
        for name in mcp_servers:
            server = await server_repo.get_by_name(name or "")
            if server is not None and server.enabled:
                servers[server.name] = server

        executor = ToolExecutor(context=context)
        built = await executor.build_many(enabled_tools, servers)
        return built, build_skill_prompt(skills)

    async def _assert_tools_exist(self, tool_ids: list[int]) -> None:
        if not tool_ids:
            return
        tool_repo = ToolRepository(self.session)
        found = await tool_repo.list_by_ids(list(dict.fromkeys(tool_ids)))
        missing = set(tool_ids) - {tool.id for tool in found}
        if missing:
            raise ValidationError(f"工具不存在: {sorted(missing)}")
