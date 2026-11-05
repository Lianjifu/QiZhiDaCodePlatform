"""复盘记录的接口模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta


class RetrospectiveOut(BaseModel):
    """一条复盘记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: str | None = None
    task_summary: str = ""
    status: str = ""
    reject_reason: str | None = None
    carrier: str = ""
    title: str = ""
    content: str = ""
    kind: str = ""
    tags: list[str] = Field(default_factory=list)
    target_id: str = ""
    target_skill_id: int | None = None
    reason: str = ""
    capability_keys: list[str] = Field(default_factory=list)
    invocation_count: int = 0
    created_at: datetime | None = None
    applied_at: datetime | None = None


class RetrospectiveListOut(BaseModel):
    """复盘记录的分页结果。"""

    items: list[RetrospectiveOut] = Field(default_factory=list)
    meta: PageMeta


class RecurrenceOut(BaseModel):
    """一条改进的复发率。

    ``failures_before`` 是改进之前同类失败过几次，``failures_after`` 是改进
    之后又失败了几次 —— 后者比前者少，才说明改进起作用了。
    """

    retrospective_id: int
    thread_id: str | None = None
    capability_keys: list[str] = Field(default_factory=list)
    failures_before: int = 0
    failures_after: int = 0
    recurrence_rate: float | None = None


class RetrospectiveMetricsOut(BaseModel):
    """复盘这条链路的度量。"""

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_reject_reason: dict[str, int] = Field(default_factory=dict)
    recurrence: list[RecurrenceOut] = Field(default_factory=list)


class RevertOut(BaseModel):
    """撤销的结果。"""

    id: int
    reverted: bool = False
    message: str = ""


__all__ = [
    "RecurrenceOut",
    "RevertOut",
    "RetrospectiveListOut",
    "RetrospectiveMetricsOut",
    "RetrospectiveOut",
]
