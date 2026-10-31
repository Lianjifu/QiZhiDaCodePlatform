"""pytest 夹具：使用内存 SQLite 跑通全链路。"""

import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.db.session import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Example  # noqa: E402,F401  # 确保模型已注册到 Base.metadata


@pytest_asyncio.fixture
async def engine():
    """每个测试用例独立的内存库（StaticPool 保证所有连接共享同一个内存库）。"""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """覆盖数据库依赖，返回可直接发请求的客户端。"""
    app = create_app()

    async def _get_db_override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
