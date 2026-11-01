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
    """启动时准备数据库。

    - 建表：只在 ``DB_AUTO_CREATE_TABLES=true`` 时做（开发期方便）；
      正式用 Alembic 迁移时要置 False，否则"迁移"和"自动建表"会打架。
    - 内置能力同步：始终执行 —— 它是往已有表里补数据，不是改 schema。
    """
    # 导入模型以确保 Base.metadata 收集到所有表
    from app.capabilities.registration.registry import CapabilityRegistry
    from app.models import Example  # noqa: F401
    from app.models.invocation import Invocation  # noqa: F401
    from app.models.memory import MemoryItem  # noqa: F401
    from app.rag.models import RagChunk, RagCollection, RagDocument  # noqa: F401

    if settings.db_auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        logger.info(
            "跳过自动建表（DB_AUTO_CREATE_TABLES=false），请确保已执行 alembic upgrade head"
        )

    async with SessionLocal() as session:
        created = await CapabilityRegistry.sync_builtins(session)
        await session.commit()
        if created:
            logger.info("已安装 %d 个内置能力", created)


async def close_db() -> None:
    """关闭引擎连接池。"""
    await engine.dispose()
