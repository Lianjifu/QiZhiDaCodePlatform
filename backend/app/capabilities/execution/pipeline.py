"""执行层：所有能力调用都从这里走一遍。

七个阶段，顺序是刻意的 —— 便宜的确定性检查在前，昂贵的人介入在中，副作用在后：

    ① schema 校验 → ② 权限校验 → ③ 风险评估 → ④ 审批等待
    → ⑤ 实际调用 → ⑥ 结果验证 → ⑦ 结果回写

**这是唯一的执行路径**：agent 内部的工具调用、直接调 API、MCP 工具，走的都是
`execute()`。早先 agent 内部调用直接命中 LangChain 的工具函数，绕开了执行层，
于是没有日志、没有超时、没有重试、没有策略检查 —— 那条路现在被堵上了。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.capabilities.errors import CapabilityError, ErrorCode
from app.capabilities.execution.adapters import get_adapter
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.redaction import redact
from app.capabilities.execution.result import ExecutionResult
from app.capabilities.execution.risk import RiskAssessment, assess
from app.capabilities.registration.policy import PermissionLevel, RiskLevel
from app.capabilities.registration.spec import CapabilitySpec
from app.capabilities.registration.validator import (
    SchemaValidationError,
    validate_arguments,
    validate_output,
)
from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from app.capabilities.intent.intent import CapabilityIntent

logger = logging.getLogger(__name__)

# 外层超时的宽限：内层（沙箱）先超时并给出可重试的 TIMEOUT，
# 外层只是兜底，避免两边同时到点时误报。
_TIMEOUT_GRACE = 5


async def execute(intent: CapabilityIntent, context: ExecutionContext) -> ExecutionResult:
    """执行一次能力调用。"""
    started = time.perf_counter()
    stages: list[dict[str, Any]] = []
    spec = intent.spec

    # ---------- ① schema 校验（权威） ----------
    error = _check_schema(intent)
    stages.append(_stage("schema", error))
    if error is not None:
        return await _finish(intent, context, stages, error, started)

    # ---------- ② 权限校验 ----------
    error = _check_permission(intent)
    stages.append(_stage("permission", error))
    if error is not None:
        return await _finish(intent, context, stages, error, started)

    # ---------- ③ 风险评估 ----------
    assessment = assess(spec, intent.arguments)
    stages.append(
        {
            "stage": "risk",
            "ok": True,
            "risk": assessment.risk,
            "reasons": assessment.reasons,
        }
    )

    # ---------- ④ 审批等待 ----------
    decision = _decide_approval(intent, assessment)
    stages.append({"stage": "approval", "ok": decision != "denied", "decision": decision})
    if decision == "denied":
        error = CapabilityError(
            f"能力 {spec.key} 的风险等级为 {assessment.risk}，当前策略不允许自动执行",
            code=ErrorCode.RISK_BLOCKED,
        )
        return await _finish(intent, context, stages, error, started, assessment=assessment)
    if decision == "pending":
        error = CapabilityError(
            f"能力 {spec.key} 需要人工审批后才能执行（"
            f"风险 {assessment.risk}，权限要求 {spec.permission}）",
            code=ErrorCode.APPROVAL_REQUIRED,
        )
        result = await _finish(
            intent, context, stages, error, started, assessment=assessment, pending=True
        )
        return result

    # ---------- ⑤ 实际调用（含幂等、超时、重试） ----------
    try:
        result = await _invoke(intent, context)
    except CapabilityError as exc:
        stages.append(_stage("invoke", exc))
        return await _finish(intent, context, stages, exc, started, assessment=assessment)
    stages.append({"stage": "invoke", "ok": True, "duration_ms": result.duration_ms})

    # ---------- ⑥ 结果验证 ----------
    error = _verify_output(spec, result)
    stages.append(_stage("verify", error))
    if error is not None:
        return await _finish(intent, context, stages, error, started, assessment=assessment)

    # ---------- ⑦ 结果回写 ----------
    result.stages = stages
    return await _finish(
        intent, context, stages, None, started, assessment=assessment, result=result
    )


# ---------- 各阶段实现 ----------


def _check_schema(intent: CapabilityIntent) -> CapabilityError | None:
    try:
        validate_arguments(intent.spec.input_schema, intent.arguments)
    except SchemaValidationError as exc:
        return CapabilityError(
            f"调用 {intent.capability_key} 的参数不合法：{exc}",
            code=ErrorCode.SCHEMA_INVALID,
            retryable=True,
        )
    return None


def _check_permission(intent: CapabilityIntent) -> CapabilityError | None:
    spec = intent.spec
    if not spec.enabled:
        return CapabilityError(f"能力 {spec.key} 已停用", code=ErrorCode.PERMISSION_DENIED)
    allowed = intent.allowed_keys
    if allowed is not None and spec.key not in allowed:
        return CapabilityError(
            f"能力 {spec.key} 不在本次允许的能力集内", code=ErrorCode.PERMISSION_DENIED
        )
    return None


def _decide_approval(intent: CapabilityIntent, assessment: RiskAssessment) -> str:
    """审批决策：``approved`` / ``pending`` / ``denied``。

    - ``permission=admin``：一律要人批；
    - ``permission=confirm``：strict 模式要人批；风险被参数级规则顶到破坏性也要人批；
    - 信任模式只能放宽 ``confirm``，放宽不了 ``admin``。
    """
    spec = intent.spec
    permission = spec.permission or PermissionLevel.NONE

    if intent.pre_approved:
        # 已经人工批准过（审批接口执行挂起调用时走这条路）
        return "approved"
    if permission == PermissionLevel.ADMIN:
        return "pending"
    if permission != PermissionLevel.CONFIRM:
        return "approved"
    if assessment.require_admin or assessment.risk == RiskLevel.DESTRUCTIVE:
        return "pending"

    mode = (intent.trust_mode or settings.capability_trust_mode).lower()
    if mode == "strict":
        return "pending"
    if mode == "permissive":
        return "approved"
    # balanced：read/write 自动放行，execute/network 要确认
    if assessment.risk in (RiskLevel.EXECUTE, RiskLevel.NETWORK):
        return "pending"
    return "approved"


async def _invoke(intent: CapabilityIntent, context: ExecutionContext) -> ExecutionResult:
    """实际调用：幂等去重 → 超时 → 重试。"""
    spec = intent.spec

    # 幂等：命中最近的同键成功记录就直接复用结果，不再执行一遍副作用
    if intent.idempotency_key and context.session is not None:
        from app.repositories.invocation import InvocationRepository

        repo = InvocationRepository(context.session)
        previous = await repo.find_recent_success(intent.idempotency_key, intent.thread_id)
        if previous is not None:
            logger.info("幂等命中，复用已有结果: %s", intent.capability_key)
            stored = previous.result or {}
            # 存下来的是整条结果，能力自己的数据在 data 里
            data = dict(stored.get("data") or {})
            return ExecutionResult(
                ok=True,
                content=str(stored.get("content") or data.get("content") or ""),
                data=data,
                duration_ms=0,
            )

    adapter = get_adapter(spec.source_type)
    attempts = max(1, spec.retry.max_attempts or 1)
    timeout = (spec.timeout_s or 30) + _TIMEOUT_GRACE
    last_error: CapabilityError | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(
                adapter.invoke(spec, intent.arguments, context), timeout=timeout
            )
        except CapabilityError as exc:
            last_error = exc
        except TimeoutError:
            last_error = CapabilityError(
                f"执行超时（>{timeout}s），已终止", code=ErrorCode.TIMEOUT, retryable=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底
            last_error = CapabilityError(str(exc) or exc.__class__.__name__, code=ErrorCode.UNKNOWN)

        # 声明可重试 与 运行时可重试 取交集
        can_retry = (
            attempt < attempts
            and last_error is not None
            and last_error.retryable
            and last_error.code.value in spec.retry.retry_on
        )
        if not can_retry:
            break
        if spec.retry.backoff_s:
            await asyncio.sleep(spec.retry.backoff_s)

    raise last_error or CapabilityError("执行失败", code=ErrorCode.UNKNOWN)


def _verify_output(spec: CapabilitySpec, result: ExecutionResult) -> CapabilityError | None:
    """结果验证：契约校验 + 效果确认。"""
    if not spec.output_schema:
        return None
    try:
        validate_output(spec.output_schema, result.data)
    except SchemaValidationError as exc:
        return CapabilityError(
            f"能力 {spec.key} 的结果不符合声明的输出契约：{exc}",
            code=ErrorCode.OUTPUT_INVALID,
        )
    return None


# ---------- 调用记录 ----------


async def _finish(
    intent: CapabilityIntent,
    context: ExecutionContext,
    stages: list[dict[str, Any]],
    error: CapabilityError | None,
    started: float,
    *,
    assessment: RiskAssessment | None = None,
    pending: bool = False,
    result: ExecutionResult | None = None,
) -> ExecutionResult:
    """写回调用记录，并组装最终结果。"""
    elapsed = int((time.perf_counter() - started) * 1000)
    spec = intent.spec

    if result is None:
        result = ExecutionResult.failed(error or CapabilityError("执行失败"), elapsed)
    else:
        result.duration_ms = elapsed
    result.stages = stages

    session = context.session
    if session is None:
        return result

    from datetime import UTC, datetime

    from app.models.invocation import (
        STATUS_FAILED,
        STATUS_PENDING,
        STATUS_SUCCEEDED,
        Invocation,
    )

    if pending:
        status = STATUS_PENDING
    elif error is not None or result.error is not None:
        status = STATUS_FAILED
    else:
        status = STATUS_SUCCEEDED

    reason = "；".join(assessment.reasons) if assessment else ""
    if pending and not reason:
        reason = f"需要人工审批（风险 {spec.risk}，权限要求 {spec.permission}）"

    record = Invocation(
        thread_id=intent.thread_id or None,
        capability_key=spec.key,
        arguments=redact(intent.arguments),
        payload=dict(intent.arguments),
        intent_fingerprint=intent.fingerprint(),
        idempotency_key=intent.idempotency_key,
        status=status,
        risk=(assessment.risk if assessment else spec.risk),
        permission=spec.permission,
        decision_reason=reason,
        stages=stages,
        result=redact(result.to_dict()),
        error_code=result.error.code.value if result.error else None,
        error_message=result.error.message if result.error else None,
        duration_ms=elapsed,
        started_at=datetime.now(UTC),
        decided_at=None if pending else datetime.now(UTC),
        finished_at=None if pending else datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    result.invocation_id = record.id
    return result


def _stage(name: str, error: CapabilityError | None) -> dict[str, Any]:
    return {
        "stage": name,
        "ok": error is None,
        "error_code": error.code.value if error else None,
        "message": error.message if error else None,
    }


__all__ = ["execute"]
