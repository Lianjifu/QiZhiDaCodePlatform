"""API 层：依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.agent import AgentService
from app.services.example import ExampleService
from app.services.llm_provider import LLMProviderService
from app.services.mcp_server import MCPServerService
from app.services.skill import SkillService
from app.services.tool import ToolService

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_example_service(session: DbSession) -> ExampleService:
    """构造示例服务实例。"""
    return ExampleService(session)


def get_agent_service(session: DbSession) -> AgentService:
    """构造编码助手服务实例。"""
    return AgentService(session)


def get_llm_provider_service(session: DbSession) -> LLMProviderService:
    """构造供应商配置服务实例。"""
    return LLMProviderService(session)


def get_mcp_server_service(session: DbSession) -> MCPServerService:
    """构造 MCP 服务器服务实例。"""
    return MCPServerService(session)


def get_tool_service(session: DbSession) -> ToolService:
    """构造工具服务实例。"""
    return ToolService(session)


def get_skill_service(session: DbSession) -> SkillService:
    """构造技能服务实例。"""
    return SkillService(session)
