"""API 层：依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.agent import AgentService
from app.services.example import ExampleService
from app.services.llm_provider import LLMProviderService

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
