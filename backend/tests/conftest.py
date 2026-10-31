"""pytest 夹具：使用内存 SQLite 跑通全链路。"""

import os
import tempfile
from collections.abc import AsyncGenerator

import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# agent 相关：测试用内存 checkpointer，工作区放临时目录，不污染仓库
os.environ.setdefault("AGENT_CHECKPOINTER_DB", ":memory:")
os.environ.setdefault("WORKSPACE_ROOT", os.path.join(tempfile.gettempdir(), "qzd-test-workspace"))
# API Key 加密主密钥：每个测试会话现生成一把，不依赖本机 .env
os.environ.setdefault("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())

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
async def app(session: AsyncSession):
    """创建应用实例并覆盖数据库依赖（测试里还可以再覆盖别的依赖）。"""
    application = create_app()

    async def _get_db_override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    application.dependency_overrides[get_db] = _get_db_override
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """返回可直接发请求的客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
