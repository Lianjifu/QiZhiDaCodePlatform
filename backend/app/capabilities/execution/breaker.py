"""熔断器：按能力统计滑动窗口内的错误率与延迟，坏了就快速失败。

为什么要有：能力挂掉时（外部接口 500、容器起不来），每次调用都要等满超时，
一轮 agent 能把时间全耗在同一个坏能力上。熔断打开后直接快速失败，
让 agent 换条路。

窗口与阈值按能力维度分开统计 —— 一个 HTTP 能力挂了，不该影响文件能力。

**这是进程内的状态**：多副本部署时每个副本各算各的。要跨副本共享得挪到
Redis 之类的地方，现在还没那个必要。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MIN_SAMPLES = 5
DEFAULT_ERROR_RATE = 0.5
DEFAULT_COOLDOWN_SECONDS = 30.0
# 慢调用也算失败的一部分：延迟超过这个值就记一次"劣化"
DEFAULT_LATENCY_MS = 5000.0


@dataclass
class _Window:
    """单个能力的滑动窗口样本。

    样本是 ``(时间戳, 是否失败)`` 的队列；``opened_until`` 是熔断打开的截止
    时间（0 表示没打开）。

    **慢调用单独记**（``degraded``）：它不是失败。
    """

    samples: deque[tuple[float, bool]] = field(default_factory=deque)
    opened_until: float = 0.0
    degraded: int = 0

    def prune(self, now: float, window: float) -> None:
        """丢掉窗口外的旧样本。"""
        threshold = now - window
        while self.samples and self.samples[0][0] < threshold:
            self.samples.popleft()


class CircuitBreaker:
    """滑动窗口熔断器。

    按能力维度（key）分别统计，互不影响 —— 一个 HTTP 能力挂了不该影响文件能力。
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        error_rate: float = DEFAULT_ERROR_RATE,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        latency_ms: float = DEFAULT_LATENCY_MS,
    ) -> None:
        self.window_seconds = window_seconds  # 滑动窗口长度（秒）
        self.min_samples = min_samples  # 样本数够多少才开始判定
        self.error_rate = error_rate  # 错误率阈值
        self.cooldown_seconds = cooldown_seconds  # 熔断打开后的冷却时间
        self.latency_ms = latency_ms  # 超过这个耗时就记一次"劣化"
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def is_open(self, key: str) -> bool:
        """熔断是否打开（打开就该快速失败）。"""
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                return False
            if window.opened_until and now < window.opened_until:
                return True
            if window.opened_until:
                # 冷却结束：半开，放一次调用过去试试
                window.opened_until = 0.0
                window.samples.clear()
            return False

    def record(self, key: str, ok: bool, duration_ms: int) -> None:
        """记一次调用结果，必要时打开熔断。

        **慢不等于失败**。一次成功但慢的调用（比如 ``run_python`` 正常跑 6 秒）
        照样把活干完了 —— 把它记成失败，五次正常执行就能把能力误熔断，
        agent 后面每次调它都被快速失败，比慢更糟。

        所以慢调用单独记一笔（``degraded``），只用于事后观察，**不参与错误率**。
        熔断只对**真失败**开：能力真的坏了才该快速失败。
        """
        now = time.monotonic()
        with self._lock:
            window = self._windows.setdefault(key, _Window())
            window.prune(now, self.window_seconds)
            if ok and duration_ms > self.latency_ms:
                window.degraded += 1
            window.samples.append((now, not ok))
            # 样本不够就不判定 —— 一次失败就熔断太敏感
            if len(window.samples) < self.min_samples:
                return
            failures = sum(1 for _ts, failed in window.samples if failed)
            rate = failures / len(window.samples)
            if rate >= self.error_rate and not window.opened_until:
                window.opened_until = now + self.cooldown_seconds

    def reset(self, key: str) -> None:
        """清掉某个能力的窗口（人工介入后用）。"""
        with self._lock:
            self._windows.pop(key, None)

    def state(self, key: str) -> dict:
        """当前状态，给排障用。"""
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                return {"samples": 0, "error_rate": 0.0, "open": False}
            window.prune(now, self.window_seconds)
            failures = sum(1 for _ts, failed in window.samples if failed)
            return {
                "samples": len(window.samples),
                "error_rate": failures / len(window.samples) if window.samples else 0.0,
                "open": bool(window.opened_until and now < window.opened_until),
                # 慢调用单独报 —— 它不参与错误率，但值得知道
                "degraded": window.degraded,
            }


# 全局单例：熔断状态要在请求之间保持
_breaker: CircuitBreaker | None = None
_breaker_lock = threading.Lock()


def get_breaker() -> CircuitBreaker:
    """全局熔断器（按配置构造一次）。

    双重检查锁：读一次全局变量在无锁情况下已经能拿到绝大多数情况，
    加锁只在第一次构造时才有意义。
    """
    global _breaker
    if _breaker is None:
        with _breaker_lock:
            if _breaker is None:
                from app.core.config import settings

                _breaker = CircuitBreaker(
                    window_seconds=settings.capability_breaker_window,
                    min_samples=settings.capability_breaker_min_samples,
                    error_rate=settings.capability_breaker_error_rate,
                    cooldown_seconds=settings.capability_breaker_cooldown,
                    latency_ms=settings.capability_breaker_latency_ms,
                )
    return _breaker


__all__ = ["CircuitBreaker", "get_breaker"]
