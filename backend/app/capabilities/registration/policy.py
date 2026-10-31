"""能力治理策略：风险等级、权限要求、重试策略、工具注解。

这些是**声明**，不是运行时判定：注册层把"这个能力有多危险、要不要人批"写清楚，
执行层按声明 + 参数级规则做最终判定。

风险与权限是**两个正交维度**：

- ``risk`` —— 这能力最坏能造成什么（读 / 写 / 执行 / 联网 / 破坏）；
- ``permission`` —— 谁来放行（自动 / 需确认 / 需管理员）。

早先把两者压成一个 ``permission: low|high``，结果"高风险但可自动放行"和
"低风险但要人批"都表达不出来。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    """风险等级：能力最坏能造成什么。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DESTRUCTIVE = "destructive"


class PermissionLevel(StrEnum):
    """权限要求：谁能放行这次调用。"""

    NONE = "none"  # 自动放行
    CONFIRM = "confirm"  # 需要人确认
    ADMIN = "admin"  # 需要管理员


# 风险由低到高。参数级规则只能把风险**上调**，不能下调。
_RISK_ORDER: dict[str, int] = {
    RiskLevel.READ: 0,
    RiskLevel.WRITE: 1,
    RiskLevel.EXECUTE: 2,
    RiskLevel.NETWORK: 3,
    RiskLevel.DESTRUCTIVE: 4,
}


def risk_rank(risk: str) -> int:
    """风险的排序值。未知值按最低处理。"""
    return _RISK_ORDER.get(str(risk), 0)


def max_risk(*risks: str) -> str:
    """取最高的风险等级。"""
    if not risks:
        return RiskLevel.READ
    return max(risks, key=risk_rank)


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略。

    声明的"可以重试"只是一半：真正是否重试，要同时满足三个条件：

    1. 运行时错误自己的 ``retryable`` 为真（声明允许 ≠ 运行时值得）；
    2. 错误码在 ``retry_on`` 白名单里；
    3. **能力是幂等的**（``require_idempotent`` 为真时）—— 不幂等的操作重试
       可能把副作用做两遍。``run_bash`` 超时就是典型：超时只说明"不知道执行完
       没有"，重跑可能重复执行。
    """

    max_attempts: int = 1  # 含首次调用
    backoff_s: float = 0.0  # 退避基数；指数退避时按 2^(n-1) 增长
    retry_on: tuple[str, ...] = ()  # ErrorCode 白名单
    exponential: bool = True  # 是否指数退避
    max_backoff_s: float = 30.0  # 单次退避上限
    require_idempotent: bool = True  # 只对幂等能力自动重试

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        data = data or {}
        raw_on = data.get("retry_on") or ()
        if isinstance(raw_on, str):
            raw_on = (raw_on,)
        return cls(
            max_attempts=max(1, int(data.get("max_attempts") or 1)),
            backoff_s=float(data.get("backoff_s") or 0.0),
            retry_on=tuple(str(item) for item in raw_on),
            exponential=bool(data.get("exponential", True)),
            max_backoff_s=float(data.get("max_backoff_s") or 30.0),
            require_idempotent=bool(data.get("require_idempotent", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_s": self.backoff_s,
            "retry_on": list(self.retry_on),
            "exponential": self.exponential,
            "max_backoff_s": self.max_backoff_s,
            "require_idempotent": self.require_idempotent,
        }

    def backoff_for(self, attempt: int) -> float:
        """第 attempt 次失败后该等多久。

        ``attempt`` 从 1 开始（第一次失败后等一次）。指数退避：base * 2^(n-1)，
        并封顶在 ``max_backoff_s``。
        """
        if self.backoff_s <= 0:
            return 0.0
        if not self.exponential:
            return min(self.backoff_s, self.max_backoff_s)
        delay = self.backoff_s * (2 ** max(0, attempt - 1))
        return min(delay, self.max_backoff_s)


@dataclass(frozen=True)
class Annotations:
    """工具自述的性质，词汇对齐 MCP tool annotations。

    与 risk 的关系：annotations 描述"工具本来是什么样"，risk 描述"平台判定这次
    调用有多危险"。注册时按 annotations 推导 risk 的**下界**，平台可以上调，
    不允许下调。
    """

    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Annotations:
        data = data or {}
        return cls(
            read_only=bool(data.get("read_only", False)),
            destructive=bool(data.get("destructive", False)),
            idempotent=bool(data.get("idempotent", False)),
            open_world=bool(data.get("open_world", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "destructive": self.destructive,
            "idempotent": self.idempotent,
            "open_world": self.open_world,
        }

    @classmethod
    def from_mcp(cls, annotations: Any) -> Annotations:
        """从 MCP server 侧的 tool annotations 继承，不用人工填。"""
        if annotations is None:
            return cls()
        return cls(
            read_only=bool(getattr(annotations, "readOnlyHint", False)),
            destructive=bool(getattr(annotations, "destructiveHint", False)),
            idempotent=bool(getattr(annotations, "idempotentHint", False)),
            open_world=bool(getattr(annotations, "openWorldHint", False)),
        )

    def derived_risk(self) -> str:
        """注解推出的风险参考值（仅供参考，不是判定依据）。

        注解描述的是"工具本来是什么样"，不是"这次调用有多危险" ——
        真要判定，看 `risk` 声明 + 参数级规则。
        """
        if self.read_only:
            return RiskLevel.READ
        if self.open_world:
            return RiskLevel.NETWORK
        return RiskLevel.WRITE


__all__ = [
    "Annotations",
    "PermissionLevel",
    "RetryPolicy",
    "RiskLevel",
    "max_risk",
    "risk_rank",
]
