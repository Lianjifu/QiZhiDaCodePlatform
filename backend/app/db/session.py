"""数据库引擎与会话管理。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个会话，异常时回滚。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """启动时建表（开发环境用，生产请用 Alembic 迁移）。

    建表之后同步内置能力的"出厂证" —— 让数据库成为能力清单的唯一事实来源，
    代码里的出厂源只负责首次安装。
    """
    # 导入模型以确保 Base.metadata 收集到所有表
    from app.capabilities.registry import CapabilityRegistry
    from app.models import Example  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        created = await CapabilityRegistry.sync_builtins(session)
        await session.commit()
        if created:
            logger.info("已安装 %d 个内置能力", created)


async def close_db() -> None:
    """关闭引擎连接池。"""
    await engine.dispose()
