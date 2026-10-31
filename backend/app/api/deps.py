"""API 层：依赖注入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.example import ExampleService

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_example_service(session: DbSession) -> ExampleService:
    """构造示例服务实例。"""
    return ExampleService(session)
