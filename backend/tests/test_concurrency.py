"""并发闸门与审批过期。

两个都是"配置里有、代码里没有"的东西 ——
`capability_max_concurrency` 与 `capability_approval_timeout` 以前都只是
配置项，没有任何地方读它们。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.capabilities.execution.concurrency import ConcurrencyLimiter
from app.models.invocation import STATUS_EXPIRED, STATUS_PENDING, Invocation
from app.services.invocation import expire_stale_approvals

# ---------- 并发闸门 ----------


@pytest.mark.asyncio
async def test_limiter_caps_parallel_acquisitions() -> None:
    """上限是 2 时，最多两个能同时拿到名额。"""
    limiter = ConcurrencyLimiter(limit=2)
    running = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal running, peak
        assert await limiter.acquire("t1")
        async with lock:
            running += 1
            peak = max(peak, running)
        await asyncio.sleep(0.05)
        async with lock:
            running -= 1
        limiter.release("t1")

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak <= 2, f"同时跑的不该超过 2 个：{peak}"
    assert limiter.snapshot("t1")["running"] == 0


@pytest.mark.asyncio
async def test_limiter_is_per_session() -> None:
    """一个会话占满名额，不该挡住别的会话。"""
    limiter = ConcurrencyLimiter(limit=1)
    assert await limiter.acquire("a")
    # 另一个会话照样能拿到
    assert await limiter.acquire("b")
    limiter.release("a")
    limiter.release("b")


@pytest.mark.asyncio
async def test_limiter_times_out_instead_of_waiting_forever() -> None:
    """名额等不到就返回 False（调用方按"繁忙"失败），不能死等。"""
    limiter = ConcurrencyLimiter(limit=1)
    assert await limiter.acquire("t")
    started = asyncio.get_running_loop().time()
    got = await limiter.acquire("t", timeout=0.1)
    assert got is False
    assert asyncio.get_running_loop().time() - started < 2
    limiter.release("t")


@pytest.mark.asyncio
async def test_limiter_zero_means_unlimited() -> None:
    """上限配成 0 表示不限。"""
    limiter = ConcurrencyLimiter(limit=0)
    for _ in range(5):
        assert await limiter.acquire("t")
        limiter.release("t")


# ---------- 审批过期 ----------


@pytest.mark.asyncio
async def test_expire_stale_approvals_marks_old_pending(session) -> None:
    """超时的 pending 记录被置为 expired，新的不动。"""
    old = Invocation(
        thread_id="t-expire",
        capability_key="run_python",
        status=STATUS_PENDING,
        created_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    fresh = Invocation(
        thread_id="t-expire",
        capability_key="run_python",
        status=STATUS_PENDING,
        created_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    session.add_all([old, fresh])
    await session.commit()

    count = await expire_stale_approvals(timeout_seconds=300, session=session)
    assert count == 1, f"应该只过期一条：{count}"

    await session.refresh(old)
    await session.refresh(fresh)
    assert old.status == STATUS_EXPIRED
    assert fresh.status == STATUS_PENDING
