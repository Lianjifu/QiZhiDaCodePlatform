"""技能仓储，含技能 ↔ 工具的关联维护。"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.models.skill import Skill
from app.models.skill_tool import SkillTool
from app.repositories.base import BaseRepository


class SkillRepository(BaseRepository[Skill]):
    model = Skill

    async def get_by_name(self, name: str) -> Skill | None:
        stmt = select(Skill).where(Skill.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Skill]:
        stmt = select(Skill).order_by(Skill.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_tool_ids(self, skill_id: int) -> list[int]:
        stmt = select(SkillTool.tool_id).where(SkillTool.skill_id == skill_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_skills_for_tool(self, tool_id: int) -> list[Skill]:
        stmt = (
            select(Skill)
            .join(SkillTool, SkillTool.skill_id == Skill.id)
            .where(SkillTool.tool_id == tool_id)
            .order_by(Skill.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def replace_tools(self, skill_id: int, tool_ids: list[int]) -> None:
        """把技能的关联工具整体替换为给定集合。"""
        stmt = delete(SkillTool).where(SkillTool.skill_id == skill_id)
        await self.session.execute(stmt)
        for tool_id in dict.fromkeys(tool_ids):  # 去重且保序
            self.session.add(SkillTool(skill_id=skill_id, tool_id=tool_id))
        await self.session.flush()

    async def remove_tool_globally(self, tool_id: int) -> None:
        """工具被删除时，解除它与所有技能的关联。"""
        stmt = delete(SkillTool).where(SkillTool.tool_id == tool_id)
        await self.session.execute(stmt)
        await self.session.flush()
