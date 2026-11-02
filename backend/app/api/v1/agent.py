"""编码助手接口。

提供两种交互方式：
- `POST /agent/chat`：跑完一轮再整体返回，适合脚本调用；
- `POST /agent/stream`：SSE 流式推送，适合前端边生成边显示。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents.runner import AgentRunResult
from app.api.deps import get_agent_service
from app.schemas.agent import (
    AgentChatOut,
    AgentChatRequest,
    AgentHistoryOut,
    PendingApprovalOut,
    ToolCallOut,
)
from app.schemas.common import ResponseModel
from app.services.agent import AgentService

router = APIRouter(prefix="/agent", tags=["编码助手"])

ServiceDep = Annotated[AgentService, Depends(get_agent_service)]


@router.post(
    "/chat",
    response_model=ResponseModel[AgentChatOut],
    summary="与编码助手对话（一轮）",
)
async def chat(
    payload: AgentChatRequest,
    service: ServiceDep,
) -> ResponseModel[AgentChatOut]:
    result = await service.chat(
        payload.message,
        payload.thread_id,
        payload.provider_id,
        payload.skill_ids,
        payload.attachments,
    )
    return ResponseModel[AgentChatOut](data=_to_out(result))


def _to_out(result: AgentRunResult) -> AgentChatOut:
    """把一轮执行结果整理成响应体。

    三种状态都走这里：跑完了、挂起等审批、审批后恢复 —— 字段结构一样，
    前端不用为每种情况写一套解析。
    """
    return AgentChatOut(
        thread_id=result.thread_id,
        content=result.content,
        steps=result.steps,
        tool_calls=[ToolCallOut(**_tool_call_fields(call)) for call in result.tool_calls],
        status=result.status,
        pending=[PendingApprovalOut(**item) for item in result.pending],
    )


def _tool_call_fields(call: dict[str, Any]) -> dict[str, Any]:
    """把 runner 收集到的调用整理成响应字段。

    只认白名单里的键 —— 摘要来自调用记录，别的一概不带出去。
    """
    allowed = (
        "name",
        "args",
        "tool_call_id",
        "invocation_id",
        "ok",
        "status",
        "error_code",
        "duration_ms",
        "blocked",
    )
    return {key: call[key] for key in allowed if key in call}


@router.get(
    "/{thread_id}/pending",
    response_model=ResponseModel[list[PendingApprovalOut]],
    summary="待审批的调用",
)
async def pending(
    thread_id: str,
    service: ServiceDep,
) -> ResponseModel[list[PendingApprovalOut]]:
    """这个会话当前挂着哪些待审批的调用。

    审批状态在 LangGraph 的 checkpoint 里 —— 图挂在哪儿只有它知道。
    """
    items = await service.pending_approvals(thread_id)
    return ResponseModel[list[PendingApprovalOut]](
        data=[PendingApprovalOut(**item) for item in items]
    )


@router.post(
    "/{thread_id}/approve",
    response_model=ResponseModel[AgentChatOut],
    summary="批准挂起的调用，恢复 agent",
)
async def approve(
    thread_id: str,
    service: ServiceDep,
) -> ResponseModel[AgentChatOut]:
    """批准挂起的调用，图继续跑，agent 自己接着往下做。"""
    result = await service.approve(thread_id)
    return ResponseModel[AgentChatOut](data=_to_out(result))


@router.post(
    "/{thread_id}/reject",
    response_model=ResponseModel[AgentChatOut],
    summary="拒绝挂起的调用，恢复 agent",
)
async def reject(
    thread_id: str,
    service: ServiceDep,
) -> ResponseModel[AgentChatOut]:
    """拒绝挂起的调用：工具不执行，agent 收到拒绝消息后继续。"""
    result = await service.reject(thread_id)
    return ResponseModel[AgentChatOut](data=_to_out(result))


@router.post("/stream", summary="与编码助手对话（SSE 流式）")
async def stream(payload: AgentChatRequest, service: ServiceDep) -> StreamingResponse:
    """流式对话。

    **一帧一种事件** —— 帧里的 ``type`` 决定它是什么：

    | type | 载荷 | 前端拿它做什么 |
    | --- | --- | --- |
    | ``text`` | ``text`` | 打字机渲染 |
    | ``tool_call`` | ``tool_call`` | 工具折叠块亮起 |
    | ``tool_result`` | ``tool_result`` | 工具折叠块补成完成态 |
    | ``pending_approval`` | ``pending`` | 弹审批卡片 |
    | ``done`` | ``status`` | 收尾 |

    帧尾是 ``data: [DONE]``，表示流结束。
    """

    async def event_stream():
        async for event in service.stream(
            payload.message,
            payload.thread_id,
            payload.provider_id,
            payload.skill_ids,
            payload.attachments,
        ):
            yield f"data: {event.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{thread_id}/history",
    response_model=ResponseModel[AgentHistoryOut],
    summary="读会话历史",
)
async def history(
    thread_id: str,
    service: ServiceDep,
) -> ResponseModel[AgentHistoryOut]:
    """这个会话的历史消息。

    从 checkpoint 的状态里读回来 —— 前端刷新页面后靠它把对话还原出来。
    """
    result = await service.history(thread_id)
    return ResponseModel[AgentHistoryOut](data=result)
