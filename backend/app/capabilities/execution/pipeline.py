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
from app.capabilities.execution.breaker import get_breaker
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.redaction import redact
from app.capabilities.execution.result import ExecutionResult
from app.capabilities.execution.risk import RiskAssessment, assess
from app.capabilities.execution.task_state import TaskState, load_task_state
from app.capabilities.registration.policy import APPROVAL_APPROVED, approval_decision
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
    """执行一次能力调用 —— 能力模块的**唯一执行入口**。

    七个阶段，顺序是刻意的（便宜的确定性检查在前，昂贵的人介入在中，副作用在后）：

    - **⓪ 任务状态**：读置信度 / 重试阈值 / 是否已阻塞；已阻塞就直接转人工，
      不再往下走；
    - **① schema 校验**（权威）：参数对不对得上声明；
    - **② 权限校验**：能力停用了没、在不在本次允许集里；
    - **③ 风险评估**：声明的风险 + 参数级规则（破坏性命令、SSRF），只能上调；
    - **④ 审批等待**：按 permission + 信任模式决定放行 / 挂起 / 拒绝；
    - **⑤ 实际调用**：幂等去重 → 超时 → 重试 → 适配器执行；
    - **⑥ 结果验证**：契约校验（output_schema）+ 效果确认（副作用真的发生了吗）；
    - **⑦ 结果回写**：写一条 ``invocation``，含各阶段结论与置信度。

    任何一个阶段失败都会**走到 ⑦** —— 失败的调用同样要留痕，否则出问题就查不到。

    Args:
        intent: 意图层翻译出来的调用意图（含声明与策略快照）。
        context: 执行上下文（工作区、沙箱、数据库会话）。

    Returns:
        统一格式的执行结果。失败时 ``ok=False``，``error`` 里带错误码。

    Note:
        这个函数不抛 ``CapabilityError`` —— 失败也返回结果对象，
        让调用方（middleware / API）统一处理"把失败翻译给模型"这件事。
    """
    started = time.perf_counter()
    stages: list[dict[str, Any]] = []
    spec = intent.spec

    # ---------- ⓪ 任务状态：置信度 / 重试阈值 / 阻塞 ----------
    task = await load_task_state(context.session, intent.thread_id, spec.key)
    if task.blocked:
        error = CapabilityError(
            f"任务已阻塞：{task.reason}（置信度 {task.confidence:.0f}），请转人工处理",
            code=ErrorCode.TASK_BLOCKED,
        )
        stages.append(_stage("task_state", error))
        return await _finish(intent, context, stages, error, started, task=task, blocked=True)

    # ---------- ① schema 校验（权威） ----------
    error = _check_schema(intent)
    stages.append(_stage("schema", error))
    if error is not None:
        return await _finish(intent, context, stages, error, started, task=task)

    # ---------- ② 权限校验 ----------
    error = _check_permission(intent)
    stages.append(_stage("permission", error))
    if error is not None:
        # 权限类错误不允许模型重试。但"能力没被授权给这次会话"是可以转审批的 ——
        # 直接终止并交给权限判断流程，而不是让模型反复撞墙。
        if error.code == ErrorCode.PERMISSION_DENIED and spec.enabled:
            approval_error = CapabilityError(
                f"能力 {spec.key} 不在本次授权范围内，需要人工授权后才能执行",
                code=ErrorCode.APPROVAL_REQUIRED,
            )
            stages.append(_stage("approval", approval_error))
            return await _finish(
                intent,
                context,
                stages,
                approval_error,
                started,
                task=task,
                pending=True,
            )
        return await _finish(intent, context, stages, error, started, task=task)

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
        return await _finish(
            intent, context, stages, error, started, assessment=assessment, task=task
        )
    if decision == "pending":
        error = CapabilityError(
            f"能力 {spec.key} 需要人工审批后才能执行（"
            f"风险 {assessment.risk}，权限要求 {spec.permission}）",
            code=ErrorCode.APPROVAL_REQUIRED,
        )
        result = await _finish(
            intent, context, stages, error, started, assessment=assessment, task=task, pending=True
        )
        return result

    # ---------- ⑤ 实际调用（含幂等、超时、重试） ----------
    try:
        result = await _invoke(intent, context)
    except CapabilityError as exc:
        stages.append(_stage("invoke", exc))
        return await _finish(
            intent, context, stages, exc, started, assessment=assessment, task=task
        )
    stages.append({"stage": "invoke", "ok": True, "duration_ms": result.duration_ms})

    # ---------- ⑥ 结果验证 ----------
    error = _verify_output(spec, result)
    stages.append(_stage("verify", error))
    if error is not None:
        return await _finish(
            intent, context, stages, error, started, assessment=assessment, task=task
        )

    # ---------- ⑦ 结果回写 ----------
    result.stages = stages
    return await _finish(
        intent,
        context,
        stages,
        None,
        started,
        assessment=assessment,
        result=result,
        task=task,
    )


# ---------- 各阶段实现 ----------


def _check_schema(intent: CapabilityIntent) -> CapabilityError | None:
    """① 权威 schema 校验。

    意图层也校验过一次，但那是**快速校验**（为了让模型少跑一趟）；
    这里是权威校验 —— 参数最终以这里为准。
    """
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
    """② 权限校验：能力停用没、在不在本次允许集里。"""
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
    """审批决策：``approved`` / ``pending``。

    规则本体在 ``registration/policy.py::approval_decision`` —— 意图层要提前
    告诉模型"哪些能力会要人批"，两处必须用同一套规则。

    - ``permission=admin``：一律要人批；
    - ``permission=confirm``：strict 模式要人批；风险被参数级规则顶到破坏性也要人批；
    - 信任模式只能放宽 ``confirm``，放宽不了 ``admin``。
    """
    if intent.pre_approved:
        # 已经人工批准过（审批接口执行挂起调用时走这条路）
        return APPROVAL_APPROVED
    return approval_decision(
        intent.spec.permission,
        assessment.risk,
        intent.trust_mode or settings.capability_trust_mode,
        require_admin=assessment.require_admin,
    )


async def _invoke(intent: CapabilityIntent, context: ExecutionContext) -> ExecutionResult:
    """⑤ 实际调用：幂等去重 → 熔断判断 → 超时 → 重试 → 适配器执行。

    内部顺序是刻意的：先看能不能白拿结果（幂等命中），再看这个能力是不是已经
    坏了（熔断），最后才真的动手。
    """
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
    # 外层超时 = 声明的超时 + 一点宽限：内层（沙箱）先超时并给出可重试的
    # TIMEOUT，外层只是兜底，避免两边同时到点时误报
    timeout = (spec.timeout_s or 30) + _TIMEOUT_GRACE
    last_error: CapabilityError | None = None
    breaker = get_breaker()

    # 熔断打开就直接快速失败，别把时间耗在一个已经坏掉的能力上
    if breaker.is_open(spec.key):
        raise CapabilityError(
            f"能力 {spec.key} 近期错误率过高，已熔断，请稍后再试或换条路",
            code=ErrorCode.CIRCUIT_OPEN,
            retryable=True,
        )

    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                adapter.invoke(spec, intent.arguments, context), timeout=timeout
            )
            breaker.record(
                spec.key, ok=True, duration_ms=int((time.perf_counter() - started) * 1000)
            )
            return result
        except CapabilityError as exc:
            last_error = _apply_idempotency(spec, exc)
        except TimeoutError:
            # 外层兜底超时：能力没自己报超时，就由这里兜住
            last_error = _apply_idempotency(
                spec,
                CapabilityError(f"执行超时（>{timeout}s），已终止", code=ErrorCode.TIMEOUT),
            )
        except asyncio.CancelledError:
            # 取消要原样往上抛，不能当成普通失败重试
            raise
        except Exception as exc:  # pragma: no cover - 兜底
            last_error = CapabilityError(str(exc) or exc.__class__.__name__, code=ErrorCode.UNKNOWN)

        # 失败也记熔断样本：慢的失败和快的失败一样会拖垮 agent
        breaker.record(spec.key, ok=False, duration_ms=int((time.perf_counter() - started) * 1000))

        can_retry = _can_retry(spec, last_error, attempt, attempts)
        if not can_retry:
            break
        delay = spec.retry.backoff_for(attempt)
        if delay:
            await asyncio.sleep(delay)

    raise last_error or CapabilityError("执行失败", code=ErrorCode.UNKNOWN)


def _apply_idempotency(spec: CapabilitySpec, error: CapabilityError) -> CapabilityError:
    """按能力的幂等性修正错误的 retryable。

    超时意味着"不知道执行完没有"。对**不幂等**的能力（``run_bash``、
    ``move_file``），重试可能把副作用做两遍 —— 所以不许重试，也要把这句话
    写进错误信息，免得模型拿着"可以重试"的提示自己去重复执行。

    不幂等能力的超时错误多半来自沙箱内部（它自己也在计时），所以规则统一
    收在这里，不让每个执行器各写一套。
    """
    if spec.annotations.idempotent:
        return error
    if error.code == ErrorCode.TIMEOUT:
        error.retryable = False
        if "不要重复执行" not in error.message:
            error.message += "；不确定是否已执行完，不要重复执行同样的命令"
    return error


def _can_retry(
    spec: CapabilitySpec,
    error: CapabilityError | None,
    attempt: int,
    attempts: int,
) -> bool:
    """能不能重试这次调用。四个条件都要满足。

    1. 还有次数；
    2. 运行时错误标了可重试（声明允许 ≠ 运行时值得）；
    3. 错误码在白名单里；
    4. **能力是幂等的** —— 不幂等的操作重试可能把副作用做两遍。
       ``run_bash`` 超时就是典型：超时只说明"不知道执行完没有"，重跑可能重复执行。
    """
    if attempt >= attempts or error is None:
        return False
    if not error.retryable:
        return False
    if error.code.value not in spec.retry.retry_on:
        return False
    if spec.retry.require_idempotent and not spec.annotations.idempotent:
        return False
    return True


def _verify_output(
    spec: CapabilitySpec,
    result: ExecutionResult,
) -> CapabilityError | None:
    """⑥ 结果验证：契约校验。

    结果数据对不对得上声明的 output_schema —— 对不上说明能力实现的返回值变了。

    **效果确认（副作用真的发生了吗）已经不需要**：文件类能力由 deepAgents 提供，
    它的 ``WriteResult`` / ``ReadResult`` 自带错误字段，"写没写成功"由它自己回答。
    """
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
    task: TaskState | None = None,
    blocked: bool = False,
) -> ExecutionResult:
    """⑦ 写回调用记录，并组装最终结果。

    Args:
        intent: 本次调用的意图。
        context: 执行上下文（决定能不能写库）。
        stages: 各阶段结论。
        error: 失败原因（``None`` 表示成功）。
        started: 本次调用开始的时刻（算耗时用）。
        assessment: 风险评估结果（决定记录里的风险等级）。
        pending: 是否挂起等审批。
        result: 成功时的执行结果。
        task: 任务状态（算置信度用）。
        blocked: 是否阻塞。

    Returns:
        组装好的执行结果（带 ``invocation_id``）。

    两列参数的差别很重要：**``arguments`` 是脱敏后的**（给人看、给排障看），
    **``payload`` 是原始的**（审批通过后要按它执行，动了就执行不了）。
    """
    elapsed = int((time.perf_counter() - started) * 1000)
    spec = intent.spec

    if result is None:
        result = ExecutionResult.failed(error or CapabilityError("执行失败"), elapsed)
    else:
        result.duration_ms = elapsed
    result.stages = stages
    if task is not None:
        # 记的是**本次调用之后**的置信度
        task.confidence = _adjust_confidence(task.confidence, result)
        result.confidence = task.confidence
        # 阈值一过就立刻标记阻塞 —— 状态是在调用前算的，等下一次调用才发现就晚了一步
        blocked = blocked or _crossed_block_line(task, result)
    result.blocked = blocked

    session = context.session
    if session is None:
        # 没有数据库会话就只返回结果，不留痕（直接调 API 的场景）
        return result

    from datetime import UTC, datetime

    from app.models.invocation import (
        STATUS_BLOCKED,
        STATUS_FAILED,
        STATUS_PENDING,
        STATUS_SUCCEEDED,
        Invocation,
    )

    if blocked:
        status = STATUS_BLOCKED
    elif pending:
        status = STATUS_PENDING
    elif error is not None or result.error is not None:
        status = STATUS_FAILED
    else:
        status = STATUS_SUCCEEDED

    # decision_reason 把"为什么需要人批"写清楚：审批的人要据此判断
    reason = "；".join(assessment.reasons) if assessment else ""
    if pending and not reason:
        reason = f"需要人工审批（风险 {spec.risk}，权限要求 {spec.permission}）"
    if blocked and task is not None:
        reason = f"{reason}；{task.reason}" if reason else task.reason

    record = Invocation(
        thread_id=intent.thread_id or None,
        capability_key=spec.key,
        arguments=redact(intent.arguments),  # 脱敏版：给人看
        payload=dict(intent.arguments),  # 原始版：批准后按它执行
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
        confidence=task.confidence if task is not None else None,
        started_at=datetime.now(UTC),
        decided_at=None if pending else datetime.now(UTC),  # 挂起的还没决定
        finished_at=None if pending else datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    result.invocation_id = record.id
    return result


def _crossed_block_line(task: TaskState, result: ExecutionResult) -> bool:
    """这次调用是否把任务推过了阻塞线。

    两条线：置信度低于阈值，或者同一能力的格式类错误攒够了次数。

    状态是**调用前**算的，所以这次调用本身也要判一次 —— 否则要等下一次调用
    才发现已经该转人工了。
    """
    from app.capabilities.execution.task_state import FORMAT_ERROR_CODES
    from app.core.config import settings

    error = result.error
    if error is not None and error.code.value in FORMAT_ERROR_CODES:
        if task.format_errors + 1 >= settings.capability_max_format_retries:
            task.reason = f"同一能力连续 {task.format_errors + 1} 次参数错误，已停止自动重试"
            return True
    if task.confidence < settings.capability_confidence_block_threshold:
        task.reason = (
            f"任务置信度 {task.confidence:.0f} 低于阈值 "
            f"{settings.capability_confidence_block_threshold:.0f}"
        )
        return True
    return False


def _adjust_confidence(confidence: float, result: ExecutionResult) -> float:
    """按本次调用的结果调整置信度。

    格式类错误扣得轻（模型改改参数就能对），语义类扣得重（模型的判断本身有问题），
    成功回补一点。置信度不会低于 0，也不会超过 100。
    """
    from app.capabilities.execution.task_state import (
        FORMAT_ERROR_CODES,
        FORMAT_PENALTY,
        SEMANTIC_ERROR_CODES,
        SEMANTIC_PENALTY,
        SUCCESS_REWARD,
    )

    error = result.error
    if error is None:
        return min(100.0, confidence + SUCCESS_REWARD)
    code = error.code.value
    if code in FORMAT_ERROR_CODES:
        return max(0.0, confidence - FORMAT_PENALTY)
    if code in SEMANTIC_ERROR_CODES:
        return max(0.0, confidence - SEMANTIC_PENALTY)
    return confidence


def _stage(name: str, error: CapabilityError | None) -> dict[str, Any]:
    """一个阶段的结论，写进调用记录。

    有了它就能一眼看出"这次调用卡在哪一关"。
    """
    return {
        "stage": name,
        "ok": error is None,
        "error_code": error.code.value if error else None,
        "message": error.message if error else None,
    }


__all__ = ["execute"]
