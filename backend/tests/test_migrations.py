"""迁移与模型 schema 的一致性测试。

迁移脚本手写，最容易出的问题是"迁移出来的结构和模型对不上" ——
这个测试直接对比两者，任何漂移都会让测试失败。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app.db.session import Base

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(*args: str, db_path: str) -> None:
    env = {**os.environ, "DATABASE_SYNC_URL": f"sqlite:///{db_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _schema(db_path: str) -> dict:
    """把库结构规范化成可比较的语义形式。

    列顺序、标识符引号、`DEFAULT (CURRENT_TIMESTAMP)` 的括号都是外观差异，
    比较时忽略；只比列（名/类型/可空）、索引、唯一约束、外键。
    """
    conn = sqlite3.connect(db_path)
    tables = [
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' and name != 'alembic_version' order by name"
        )
    ]
    out: dict = {}
    for table in tables:
        cols = {
            (r[1], (r[2] or "").upper(), bool(r[3]))
            for r in conn.execute(f"pragma table_info({table})")
        }
        index_rows = list(conn.execute(f"pragma index_list({table})"))
        indexes = {
            (r[1], bool(r[2])) for r in index_rows if not str(r[1]).startswith("sqlite_autoindex")
        }
        uniques = set()
        for r in index_rows:
            if not r[2]:
                continue
            uniques.add(tuple(c[2] for c in conn.execute(f"pragma index_info({r[1]})")))
        fks = {(r[3], r[2], r[4]) for r in conn.execute(f"pragma foreign_key_list({table})")}
        out[table] = {"cols": cols, "indexes": indexes, "uniques": uniques, "fks": fks}
    conn.close()
    return out


@pytest.mark.asyncio
async def test_migrations_match_models(tmp_path) -> None:
    """迁移建出来的库，必须和模型 create_all 建出来的一模一样。"""
    migrated_db = str(tmp_path / "migrated.db")
    created_db = str(tmp_path / "created.db")

    _alembic("upgrade", "head", db_path=migrated_db)

    engine = create_async_engine(f"sqlite+aiosqlite:///{created_db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    assert _schema(migrated_db) == _schema(created_db)


@pytest.mark.asyncio
async def test_migration_upgrades_legacy_data_without_loss(tmp_path) -> None:
    """旧 schema 的库升级后，数据要完整迁移到新字段。"""
    db_path = str(tmp_path / "legacy.db")

    _alembic("upgrade", "0001_baseline", db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "insert into skill (name, description, prompt, enabled, builtin, created_at, updated_at)"
        " values ('旧技能', '描述', '旧的知识', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "insert into tool (name, display_name, description, type, source, config, parameters,"
        " enabled, created_at, updated_at)"
        " values ('old_tool', '旧工具', '说明', 'builtin', 'x:y', '{}', '{}', 1,"
        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    _alembic("upgrade", "head", db_path=db_path)

    conn = sqlite3.connect(db_path)
    skill = conn.execute("select name, knowledge, version, tags from skill").fetchone()
    tool = conn.execute("select name, origin, permission, risk, version, tags from tool").fetchone()
    has_invocation = conn.execute(
        "select count(*) from sqlite_master where name='invocation'"
    ).fetchone()[0]
    has_log = conn.execute(
        "select count(*) from sqlite_master where name='execution_log'"
    ).fetchone()[0]
    conn.close()

    # 旧字段改名后内容还在
    assert skill[0] == "旧技能"
    assert skill[1] == "旧的知识"
    # 新列都回填了默认值
    assert skill[2] == 1 and skill[3] == "[]"
    assert tool[1] == "user" and tool[3] == "read" and tool[4] == 1 and tool[5] == "[]"
    # 权限取值域迁移了：low → none
    assert tool[2] == "none"
    # execution_log 改名成 invocation
    assert has_invocation == 1
    assert has_log == 0


@pytest.mark.asyncio
async def test_migration_backfills_context_window_from_preset(tmp_path) -> None:
    """存量供应商记录要按厂商预设回填上下文窗口。

    不回填的话压缩阈值会退回到"170000 token 触发、保留 6 条"，
    对 DeepSeek 这种 1M 窗口的模型来说是错的。
    """
    db_path = str(tmp_path / "window.db")

    _alembic("upgrade", "0009_memory_item", db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "insert into llm_provider (name, provider, default_model, temperature, max_tokens,"
        " timeout, is_default, is_active, available_models, embedding_model, embedding_dim,"
        " api_key_masked, created_at, updated_at)"
        " values ('DS', 'deepseek', 'deepseek-flash', 0.0, 4096, 60, 0, 1, '[]', '', 0,"
        " '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    _alembic("upgrade", "head", db_path=db_path)

    conn = sqlite3.connect(db_path)
    window = conn.execute("select context_window from llm_provider").fetchone()[0]
    conn.close()

    from app.agents.providers import get_preset

    assert window == get_preset("deepseek").context_window > 0


@pytest.mark.asyncio
async def test_migration_downgrade_restores_legacy_schema(tmp_path) -> None:
    """降级要能把新字段退回去。"""
    db_path = str(tmp_path / "downgrade.db")
    _alembic("upgrade", "head", db_path=db_path)
    _alembic("downgrade", "0001_baseline", db_path=db_path)

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("pragma table_info(skill)")]
    has_log = conn.execute(
        "select count(*) from sqlite_master where name='execution_log'"
    ).fetchone()[0]
    conn.close()

    assert "prompt" in cols and "knowledge" not in cols
    assert has_log == 0
