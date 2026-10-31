"""Alembic 运行环境。

用**异步**引擎跑迁移 —— 项目只装了 asyncpg，没有 psycopg2，所以不能走
`database_sync_url` 那条同步链路（SQLite 除外）。连接串从应用配置读，
不写在 alembic.ini 里（避免密钥进版本库）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.db.session import Base

# 导入所有模型，确保 Base.metadata 收集到全部表
from app.models import Example, ExecutionLog, LLMProvider, MCPServer, Skill, SkillTool, Tool  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """迁移用的连接串：优先用同步 URL（SQLite 场景更稳），否则用异步 URL。"""
    sync_url = (settings.database_sync_url or "").strip()
    if sync_url and sync_url.startswith("sqlite"):
        return sync_url
    return settings.database_url


def _is_sync_url(url: str) -> bool:
    """判断连接串是不是同步驱动（同步 URL 不能喂给异步引擎）。"""
    return "+aiosqlite" not in url and "+asyncpg" not in url and "+psycopg" not in url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations() -> None:
    """同步引擎（SQLite 场景用）。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    engine = create_engine(section["sqlalchemy.url"], poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            do_run_migrations(connection)
    finally:
        engine.dispose()


async def run_async_migrations() -> None:
    """在线模式：用异步引擎连库（Postgres 等）。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    if _is_sync_url(_database_url()):
        run_sync_migrations()
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
